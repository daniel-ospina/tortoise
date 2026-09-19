"""B6 (#3892) — the read path's not-silent status contract (search / recall).

One status per retrieval read, from the FOUR RECORDED terms (roadmap §7 item 9,
ADOPTED 2026-09-17): ``available | empty | degraded | unconfigured``. The
vocabulary's ONE home is ``tortoise/status_vocabulary.py`` (the client-boundary
contract); the read path consumes it and mints no term of its own. The
load-bearing property is that ``unconfigured`` (never declared) and ``degraded``
(configured but impaired) are never indistinguishable from ``empty`` (the store
answered and had nothing), and a failure is never returned as a successful
empty result.

MUTATION PROOF — the guard is shown to MOVE (the reverts and their verbatim
pytest output are recorded in the PR body / the lane report):

* **Collapse the failed path back onto empty** — make ``classify_read_status``
  return ``STATUS_EMPTY`` when the store was not reached (the #3892 defect) →
  ``TestReadPathStates::test_state_unconfigured_*``,
  ``TestClassify::test_configured_but_unreachable_is_degraded`` and
  ``test_unconfigured_is_distinguishable_from_empty`` RED.
* **Re-fork the mapping** — make the configured-but-unreachable condition
  report ``unconfigured`` again (the pre-alignment read-path mapping) → the
  ``degraded`` tests and the cross-lane parity rows RED.
* **Drop the status write** — make ``_with_read_status`` a no-op → every
  ``TestReadPathStates`` state assertion REDs (``KeyError: 'status'``) and the
  hosted-route ON test REDs.

These run on the embedded lane (``TORTOISE_TEST_CARVE_OUT=1``) or a
``TORTOISE_DB_URI`` (``tests/conftest.py`` session gate).
"""
from __future__ import annotations

import asyncio

import pytest

from tortoise import status_vocabulary
from tortoise.embeddings import EmbeddingModel
from tortoise.read_status import (
    READ_STATUSES,
    STATUS_AVAILABLE,
    STATUS_DEGRADED,
    STATUS_EMPTY,
    STATUS_UNCONFIGURED,
    classify_leg_trace,
    classify_read_status,
    combine_read_statuses,
    read_status_enabled,
)


def _leg(leg, ran, degraded, reason, count=0):
    return {"leg": leg, "ran": ran, "degraded": degraded,
            "reason": reason, "count": count}


def _no_embedder(monkeypatch):
    """The dense leg cannot run — the #2573/#2898 degrade class."""
    monkeypatch.setattr(
        EmbeddingModel, "get", classmethod(lambda cls, load_timeout=None: None))


class _UnreachableGraph:
    def query(self, *args, **kwargs):
        raise RuntimeError("connection refused")


class _UnreachableProj:
    g = _UnreachableGraph()


class TestVocabulary:
    """The vocabulary is RECORDED — these four and nothing else."""

    def test_exactly_the_four_recorded_terms(self):
        assert set(READ_STATUSES) == {
            "available", "empty", "degraded", "unconfigured"}
        assert len(READ_STATUSES) == 4

    def test_read_statuses_is_the_client_boundary_term_set(self):
        # The read path does not own a copy of the vocabulary — it re-exports
        # the recording home's published set (#3805 / PR #4044).
        assert READ_STATUSES is status_vocabulary.CLIENT_STATUS_TERMS
        assert set(status_vocabulary.CONDITIONS) == set(READ_STATUSES)

    def test_status_field_is_off_by_default(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_READ_STATUS", raising=False)
        assert read_status_enabled() is False

    @pytest.mark.parametrize("value", ["0", "", "off", "no", "false"])
    def test_falsy_values_leave_the_field_off(self, monkeypatch, value):
        monkeypatch.setenv("TORTOISE_READ_STATUS", value)
        assert read_status_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " On "])
    def test_truthy_values_turn_the_field_on(self, monkeypatch, value):
        monkeypatch.setenv("TORTOISE_READ_STATUS", value)
        assert read_status_enabled() is True


class TestCrossLaneParity:
    """The same condition names the same term on both surfaces (#3805).

    The read path and the client boundary draw from ONE home module, so this
    asserts the terms rather than the wording: for each condition BOTH trees
    can express, the read path's classifier and the boundary's must agree.
    """

    @pytest.mark.parametrize(
        "condition,read_kwargs,vocab_kwargs",
        [
            ("reached and returned content",
             dict(reached=True, hit_count=3, degraded=False),
             dict(configured=True, reached=True, hits=3)),
            ("reached and returned nothing",
             dict(reached=True, hit_count=0, degraded=False),
             dict(configured=True, reached=True, hits=0)),
            ("configured but unreachable",
             dict(configured=True, reached=False, hit_count=0, degraded=False),
             dict(configured=True, reached=False, hits=0)),
            ("never declared",
             dict(configured=False, reached=False, hit_count=0,
                  degraded=False),
             dict(configured=False, reached=False, hits=0)),
        ],
    )
    def test_read_path_term_equals_client_boundary_term(
            self, condition, read_kwargs, vocab_kwargs):
        assert (classify_read_status(**read_kwargs)
                == status_vocabulary.classify(**vocab_kwargs)), condition

    def test_a_leg_that_did_not_run_is_the_boundary_degraded_term(self):
        # The four terms name no "the store answered but not every leg did"
        # condition. It is carried as `degraded` — the recorded term for an
        # impaired memory — and no fifth term is coined for it.
        assert classify_read_status(
            reached=True, hit_count=3, degraded=True
        ) == status_vocabulary.STATUS_DEGRADED


class TestClassify:
    """The four conditions mapped to the classifier, one test per state."""

    def test_available_when_reached_with_hits(self):
        assert classify_read_status(
            reached=True, hit_count=3, degraded=False) == STATUS_AVAILABLE

    def test_empty_when_reached_with_no_hits(self):
        assert classify_read_status(
            reached=True, hit_count=0, degraded=False) == STATUS_EMPTY

    def test_degraded_when_a_leg_did_not_run(self):
        assert classify_read_status(
            reached=True, hit_count=3, degraded=True) == STATUS_DEGRADED

    def test_degraded_outranks_empty(self):
        # A leg did not run ⇒ "nothing matched" is NOT established.
        assert classify_read_status(
            reached=True, hit_count=0, degraded=True) == STATUS_DEGRADED

    def test_configured_but_unreachable_is_degraded_not_unconfigured(self):
        # B3's client boundary wins: `unconfigured` = never declared;
        # `degraded` = configured but the store did not answer.
        assert classify_read_status(
            reached=False, hit_count=0, degraded=False) == STATUS_DEGRADED
        assert classify_read_status(
            reached=False, hit_count=0, degraded=True) == STATUS_DEGRADED

    def test_never_declared_is_unconfigured(self):
        assert classify_read_status(
            configured=False, reached=False, hit_count=0,
            degraded=False) == STATUS_UNCONFIGURED

    def test_unconfigured_is_not_empty(self):
        # THE load-bearing property of the read path.
        assert classify_read_status(
            configured=False, reached=False, hit_count=0,
            degraded=False) != STATUS_EMPTY
        assert classify_read_status(
            reached=True, hit_count=0, degraded=False) == STATUS_EMPTY

    def test_breaker_open_trace_is_degraded_not_unconfigured(self):
        # A trace whose legs were all skipped by a tripped breaker is a store
        # that is configured but did not answer: `degraded`. Only a store that
        # was never declared is `unconfigured`.
        trace = [_leg("fts", False, True, "breaker_open", 0),
                 _leg("structural", False, True, "breaker_open", 0)]
        assert classify_leg_trace(trace, hit_count=0) == STATUS_DEGRADED
        assert classify_leg_trace(
            trace, hit_count=0, configured=False) == STATUS_UNCONFIGURED

    def test_leg_trace_available(self):
        trace = [_leg("fts", True, False, "ok", 2),
                 _leg("vector", True, False, "ok", 1),
                 _leg("structural", True, False, "empty_results", 0)]
        assert classify_leg_trace(trace, hit_count=2) == STATUS_AVAILABLE

    def test_leg_trace_empty(self):
        trace = [_leg("fts", True, False, "empty_results", 0),
                 _leg("structural", True, False, "empty_results", 0)]
        assert classify_leg_trace(trace, hit_count=0) == STATUS_EMPTY

    def test_leg_trace_degraded_dense_leg_skipped(self):
        trace = [_leg("vector", False, True, "no_embedder", 0),
                 _leg("fts", True, False, "ok", 1)]
        assert classify_leg_trace(trace, hit_count=1) == STATUS_DEGRADED

    def test_leg_trace_all_legs_failed_is_degraded(self):
        # The store was configured (a leg trace only exists once the read path
        # has a projection object) and no leg answered: off by OUTAGE, which
        # the recorded vocabulary names `degraded` — never `unconfigured`,
        # which is reserved for a store that was never declared.
        trace = [_leg("fts", True, True, "query_failed", 0),
                 _leg("vector", False, True, "breaker_open", 0),
                 _leg("structural", True, True, "query_failed", 0)]
        assert classify_leg_trace(trace, hit_count=0) == STATUS_DEGRADED
        assert classify_leg_trace(
            trace, hit_count=0, configured=False) == STATUS_UNCONFIGURED

    def test_probe_proof_makes_an_unanswered_read_empty_not_degraded(self):
        # No leg answered and nothing came back, but the bounded probe proved
        # the store ANSWERS: the honest term is `empty` (reached, nothing
        # matched). Without that proof the same trace derives no reachability
        # and is `degraded`.
        assert classify_leg_trace([], hit_count=0) == STATUS_DEGRADED
        assert classify_leg_trace(
            [], hit_count=0, reached=True) == STATUS_EMPTY

    def test_results_bearing_fallback_is_degraded(self):
        trace = [_leg("fts", True, False, "ok", 1),
                 _leg("fallback", True, True, "tfidf_snapshot", 2)]
        assert classify_leg_trace(trace, hit_count=2) == STATUS_DEGRADED

    def test_zero_count_fallback_is_not_degraded(self):
        # the #2952 rule: a recovery that found nothing disqualifies nothing,
        # so an honest no-match read stays `empty` on a healthy store.
        trace = [_leg("structural", True, False, "empty_results", 0),
                 _leg("fallback", True, True, "no_fallback_applicable", 0)]
        assert classify_leg_trace(trace, hit_count=0) == STATUS_EMPTY

    def test_non_dict_entries_are_ignored(self):
        assert classify_leg_trace(["junk", None, 5], hit_count=1) == STATUS_AVAILABLE


class TestCombine:
    """The composite (Points + Objects) recall read coalesces its legs."""

    def test_none_when_nothing_present(self):
        assert combine_read_statuses(None, None) is None

    def test_available_beats_empty(self):
        assert combine_read_statuses(
            STATUS_AVAILABLE, STATUS_EMPTY) == STATUS_AVAILABLE

    def test_unconfigured_beats_everything(self):
        # only when NO leg reached the store is the composite unconfigured —
        # see test_reached_leg_beats_unconfigured for the other direction
        assert combine_read_statuses(
            STATUS_UNCONFIGURED, STATUS_UNCONFIGURED) == STATUS_UNCONFIGURED
        assert combine_read_statuses(
            STATUS_UNCONFIGURED, None) == STATUS_UNCONFIGURED

    def test_reached_leg_beats_unconfigured(self):
        # P1/P2 review fix: a composite read that returned hits (or answered
        # with an empty result) cannot simultaneously report `unconfigured` —
        # the store WAS reached, so the leg that could not be reached makes the
        # read incomplete (`degraded`), not absent.
        assert combine_read_statuses(
            STATUS_AVAILABLE, STATUS_UNCONFIGURED) == STATUS_DEGRADED
        assert combine_read_statuses(
            STATUS_EMPTY, STATUS_UNCONFIGURED) == STATUS_DEGRADED
        assert combine_read_statuses(
            STATUS_DEGRADED, STATUS_UNCONFIGURED) == STATUS_DEGRADED

    def test_degraded_beats_available(self):
        assert combine_read_statuses(
            STATUS_AVAILABLE, STATUS_DEGRADED) == STATUS_DEGRADED

    def test_both_empty(self):
        assert combine_read_statuses(STATUS_EMPTY, STATUS_EMPTY) == STATUS_EMPTY


class TestReadPathStates:
    """One end-to-end test per state, on the shipped search read path."""

    def test_state_available_store_reached_and_hits_returned(
            self, sdk_factory, monkeypatch):
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            out: dict = {}
            rows = sdk.tortoise_fts_query(
                None, kind="statement", read_status_out=out, limit=5)
            assert rows
            assert out["status"] == STATUS_AVAILABLE
        finally:
            sdk.close()

    def test_state_empty_store_reached_and_nothing_matched(
            self, sdk_factory, monkeypatch):
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            out: dict = {}
            rows = sdk.tortoise_fts_query(
                None, kind="no_such_kind", read_status_out=out, limit=5)
            assert rows == []
            assert out["status"] == STATUS_EMPTY
        finally:
            sdk.close()

    def test_state_degraded_dense_leg_skipped_and_results_still_returned(
            self, sdk_factory, monkeypatch):
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            base = sdk.tortoise_fts_query("alpha beta", limit=5)
            out: dict = {}
            rows = sdk.tortoise_fts_query(
                "alpha beta", read_status_out=out, limit=5)
            assert out["status"] == STATUS_DEGRADED
            # degraded ≠ nothing: the same rows survive the degraded read
            assert rows == base
            assert rows
        finally:
            sdk.close()

    def test_state_unconfigured_no_store_is_a_status_not_an_exception(
            self, sdk_factory, monkeypatch):
        sdk = sdk_factory()
        try:
            def _boom():
                raise RuntimeError("no store/endpoint configured")

            monkeypatch.setattr(sdk, "_get_proj", _boom)
            out: dict = {}
            # MUST NOT raise, and MUST NOT be reported as an empty read.
            rows = sdk.tortoise_fts_query("alpha", read_status_out=out, limit=5)
            assert rows == []
            assert out["status"] == STATUS_UNCONFIGURED
        finally:
            sdk.close()

    def test_state_configured_but_unreachable_is_degraded(
            self, sdk_factory, monkeypatch):
        # The client boundary's mapping: `unconfigured` names a store that was
        # NEVER DECLARED, and this SDK has one (the projection object was
        # obtained) — it just did not answer. Off by outage is `degraded`.
        sdk = sdk_factory()
        try:
            monkeypatch.setattr(sdk, "_get_proj", lambda: _UnreachableProj())
            out: dict = {}
            rows = sdk.tortoise_fts_query("alpha", read_status_out=out, limit=5)
            assert rows == []
            assert out["status"] == STATUS_DEGRADED
            assert out["status"] != STATUS_UNCONFIGURED
        finally:
            sdk.close()

    def test_state_breaker_open_but_reachable_is_degraded_not_unconfigured(
            self, sdk_factory, monkeypatch):
        """P1 review fix: the probe is PROOF. A store that answers `RETURN 1`
        but whose in-process breakers are still open is a store with a skipped
        leg (`degraded`) — never `unconfigured`, which is the term reserved for
        a store that could not be reached at all.

        The store is deliberately EMPTY and no leg returns rows, so the probe's
        `reached=True` is the ONLY thing that can move this test: without the
        override the trace derives no reachability and classifies
        `unconfigured` (the P1 defect)."""
        _no_embedder(monkeypatch)
        from tortoise import search_engine

        monkeypatch.setattr(search_engine, "_breaker_allow", lambda leg: False)
        sdk = sdk_factory()
        try:
            out: dict = {}
            rows = sdk.tortoise_fts_query("alpha beta", read_status_out=out, limit=5)
            assert rows == []
            assert out["status"] == STATUS_DEGRADED
            assert out["status"] != STATUS_UNCONFIGURED
        finally:
            sdk.close()

    def test_unconfigured_is_distinguishable_from_empty(
            self, sdk_factory, monkeypatch):
        """THE load-bearing assertion of #3892."""
        sdk_u = sdk_factory()
        try:
            def _boom():
                raise RuntimeError("no store/endpoint configured")

            monkeypatch.setattr(sdk_u, "_get_proj", _boom)
            out_u: dict = {}
            rows_u = sdk_u.tortoise_fts_query(
                "alpha", read_status_out=out_u, limit=5)
        finally:
            sdk_u.close()
        sdk_e = sdk_factory()
        try:
            _no_embedder(monkeypatch)
            out_e: dict = {}
            rows_e = sdk_e.tortoise_fts_query(
                None, kind="no_such_kind", read_status_out=out_e, limit=5)
        finally:
            sdk_e.close()
        assert rows_u == rows_e == []          # identical observable results…
        assert out_u["status"] == STATUS_UNCONFIGURED
        assert out_e["status"] == STATUS_EMPTY
        assert out_u["status"] != out_e["status"]  # …distinguishable statuses

    def test_status_is_additive_and_the_default_response_is_unchanged(
            self, sdk_factory, monkeypatch):
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            base = sdk.tortoise_fts_query("alpha beta", limit=5)
            out: dict = {}
            with_status = sdk.tortoise_fts_query(
                "alpha beta", read_status_out=out, limit=5)
            # additive: asking for a status changes no returned row
            assert with_status == base
            assert out["status"] in READ_STATUSES
        finally:
            sdk.close()

    def test_recall_state_carries_the_composite_status(
            self, sdk_factory, monkeypatch):
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            out: dict = {}
            rows = sdk.recall_state(
                None, kind="no_such_kind", read_status_out=out, limit=5)
            assert rows == []
            assert out["status"] in READ_STATUSES
        finally:
            sdk.close()

    def test_recall_state_unconfigured_is_a_status_not_an_exception(
            self, sdk_factory, monkeypatch):
        sdk = sdk_factory()
        try:
            def _boom():
                raise RuntimeError("no store/endpoint configured")

            monkeypatch.setattr(sdk, "_get_proj", _boom)
            out: dict = {}
            rows = sdk.recall_state("alpha", read_status_out=out, limit=5)
            assert rows == []
            assert out["status"] == STATUS_UNCONFIGURED
        finally:
            sdk.close()


class TestHostedSearchRoute:
    """The emitted field on the shipped /v1/search response."""

    def _patch_route(self, monkeypatch, term):
        import tortoise.hosted_api as hosted_api

        class _FakeSDK:
            def tortoise_fts_query(self, q, limit=10, read_status_out=None):
                if read_status_out is not None:
                    read_status_out["status"] = term
                return [{"id": "p1", "content": "alpha", "pointKind": "statement"}]

            def close(self):
                pass

        monkeypatch.setattr(hosted_api, "_data_sdk", lambda org: _FakeSDK())
        monkeypatch.setattr(hosted_api, "_require_scope", lambda org, scope, name: None)
        return hosted_api

    def test_field_is_absent_by_default(self, monkeypatch):
        hosted_api = self._patch_route(monkeypatch, STATUS_AVAILABLE)
        monkeypatch.delenv("TORTOISE_READ_STATUS", raising=False)
        body = asyncio.run(hosted_api.search(q="alpha", limit=5, org={}))
        assert body == {"results": [{"id": "p1", "content": "alpha",
                                     "kind": "statement"}], "count": 1}
        assert "status" not in body  # byte-identical when off

    def test_field_rides_the_response_when_enabled(self, monkeypatch):
        hosted_api = self._patch_route(monkeypatch, STATUS_UNCONFIGURED)
        monkeypatch.setenv("TORTOISE_READ_STATUS", "1")
        body = asyncio.run(hosted_api.search(q="alpha", limit=5, org={}))
        assert body["status"] == STATUS_UNCONFIGURED
        assert body["count"] == 1
        assert body["results"]
