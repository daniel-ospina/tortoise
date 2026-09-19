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
  return ``STATUS_EMPTY`` when the store was not reached, **and** map the SDK's
  ``_get_proj()`` and probe-failure sites to ``empty`` (the #3892 defect) →
  ``TestClassify::test_never_declared_is_unconfigured``,
  ``test_unconfigured_is_not_empty``, the end-to-end
  ``test_state_a_store_that_will_not_open_is_degraded``,
  ``test_state_real_unreachable_server_is_degraded`` and
  ``test_engine_read_path_never_reports_unconfigured`` RED. (The classifier
  change alone does not move the SDK-level ones — those sites write their term
  directly — so the mutation has to include them; that is why the end-to-end
  tests are named here.)
* **Re-fork the mapping** — make the configured-but-unreachable condition
  report ``unconfigured`` again (the pre-alignment read-path mapping) → the
  ``degraded`` tests and the cross-lane parity rows RED.
* **Drop the status write** — make ``_with_read_status`` a no-op → every
  state assertion that ROUTES THROUGH it REDs (``KeyError: 'status'``). The
  three tests that set the term directly on the ``_get_proj()`` failure path
  (two on ``tortoise_fts_query``, one on ``recall_state``) stay green under
  that mutation, as does the hosted-route ON test — it monkeypatches
  ``_data_sdk`` with a fake that writes the term itself, so the real
  ``_with_read_status`` is never invoked.

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


def _unopenable_store(monkeypatch, sdk):
    """Make the store's OPEN fail the way a real outage does.

    ``FalkorProjection.__init__`` calls ``_auto_health_recover()``, which
    raises ``RuntimeError`` when a DECLARED server is unreachable (or a
    declared embedded file cannot be opened) — so ``_get_proj()`` raising IS
    what a configured-but-down store looks like on the read path.
    """
    def _health_check_failed():
        raise RuntimeError(
            "DB health check failed on open (server/production mode).")

    monkeypatch.setattr(sdk, "_get_proj", _health_check_failed)


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

    ``tortoise.status_vocabulary`` is the ONE home of the four terms and of
    the boundary's mapping, and the client surfaces
    (``client/tortoise_client/cli.py``, ``tortoise/tortoise_client.py``) import
    it. For each condition BOTH trees can express, these rows assert that the
    read path's classifier and the boundary's classifier return the same term.
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

    def test_the_read_path_overrides_a_contradictory_configured_false(self):
        """The ONE row the two implementations can disagree on, pinned.

        ``configured=False`` beside a read that ANSWERED is self-
        contradictory, and a read that answered proves a store was declared.
        The read path re-normalises it to ``available``/``empty``; the
        boundary's ``classify`` returns ``unconfigured`` from a flat
        configuration-first check. The read path's resolution is the intended
        one (the vocabulary's invariant), and it is recorded here rather than
        left as a silent second mapping.
        """
        assert status_vocabulary.classify(
            configured=False, reached=True, hits=3
        ) == status_vocabulary.STATUS_UNCONFIGURED
        assert classify_read_status(
            configured=False, reached=True, hit_count=3,
            degraded=False) == status_vocabulary.STATUS_AVAILABLE

    def test_the_client_surfaces_share_the_boundary_terms_and_exit_split(
            self):
        """The boundary half is the REAL client lane, not a copy.

        ``tortoise/tortoise_client.py`` (the S9 skill-wiring client) imports
        the terms from the home module, and its exit-code map is the
        #3832/D5 split the vocabulary exists to protect: ``degraded`` (off by
        outage) and ``unconfigured`` (off by policy) must stay DISTINCT. This
        asserts the client module's binding to that home and the split.
        """
        from tortoise import tortoise_client

        assert tortoise_client.status_vocabulary is status_vocabulary
        assert (tortoise_client.STATUS_DEGRADED
                == status_vocabulary.STATUS_DEGRADED)
        assert (tortoise_client.STATUS_UNCONFIGURED
                == status_vocabulary.STATUS_UNCONFIGURED)
        codes = tortoise_client._STATUS_EXIT_CODES
        assert set(codes) == set(status_vocabulary.CLIENT_STATUS_TERMS)
        assert codes[status_vocabulary.STATUS_DEGRADED] != codes[
            status_vocabulary.STATUS_UNCONFIGURED]


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

    def test_a_read_that_answered_is_never_unconfigured(self):
        # The vocabulary's invariant, enforced at the classifier itself: a
        # read that reached the store (or returned rows) PROVES a store was
        # declared, whatever the caller passed for `configured`.
        assert classify_read_status(
            configured=False, reached=True, hit_count=3,
            degraded=False) == STATUS_AVAILABLE
        assert classify_read_status(
            configured=False, reached=True, hit_count=0,
            degraded=False) == STATUS_EMPTY
        assert classify_leg_trace(
            [], hit_count=3, configured=False) == STATUS_AVAILABLE

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

    def test_state_a_store_that_will_not_open_is_degraded(
            self, sdk_factory, monkeypatch):
        # `unconfigured` names a store that was NEVER DECLARED. The engine SDK
        # always resolves a store TARGET (a server URI, or the canonical
        # embedded path via `resolve_db_path()` / `FalkorProjection`'s no-arg
        # fallback), so a store that will not open is off by OUTAGE —
        # `degraded` — and the status is the answer, not an exception.
        sdk = sdk_factory()
        try:
            _unopenable_store(monkeypatch, sdk)
            out: dict = {}
            rows = sdk.tortoise_fts_query("alpha", read_status_out=out, limit=5)
            assert rows == []
            assert out["status"] == STATUS_DEGRADED
            assert out["status"] != STATUS_UNCONFIGURED
        finally:
            sdk.close()

    def test_state_real_unreachable_server_is_degraded(
            self, monkeypatch):
        """The LIVE case the mocked one stands in for: a declared but
        unreachable server. No mock of ``_get_proj`` — the SDK opens a real
        (closed) port, the health check fails, and the status must be
        ``degraded``, never `unconfigured`."""
        from tortoise.sdk import TortoiseSDK

        monkeypatch.setenv("TORTOISE_DB_URI",
                           "docker://:pw@127.0.0.1:1/tortoise_probe")
        monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
        monkeypatch.setenv("FLY_APP_NAME", "test")  # server mode → fail loud
        sdk = TortoiseSDK()
        try:
            assert sdk._db_uri is not None  # a target IS declared
            out: dict = {}
            rows = sdk.tortoise_fts_query("alpha", read_status_out=out, limit=5)
            assert rows == []
            assert out["status"] == STATUS_DEGRADED
            assert out["status"] != STATUS_UNCONFIGURED
        finally:
            sdk.close()

    def test_state_breaker_open_but_reachable_is_degraded_not_unconfigured(
            self, sdk_factory, monkeypatch):
        """A store that answers `RETURN 1` but whose in-process breakers are
        still open is a store with a skipped leg: `degraded`. `unconfigured`
        is reserved for a store that was NEVER DECLARED, and this one has a
        declared target. The store is deliberately empty and no leg returns
        rows, so the class comes from the skipped legs themselves."""
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

    def test_engine_read_path_never_reports_unconfigured(
            self, sdk_factory, monkeypatch):
        """The alignment's honest consequence, pinned.

        `unconfigured` names a SET-UP GAP — no store / endpoint / key was ever
        declared. The engine SDK always resolves a store TARGET: a server URI,
        or the canonical embedded path via ``resolve_db_path()`` and
        ``FalkorProjection``'s no-arg fallback. So every engine read path
        lands on ``available`` / ``empty`` / ``degraded`` and never on
        ``unconfigured`` — including the two states that LOOK like it:
        a store that answered with nothing (``empty``) and a store that will
        not open (``degraded``). The term stays consumable at the classifier
        (``configured=False``); it is the client boundary that can reach it.
        """
        sdk = sdk_factory()
        try:
            _no_embedder(monkeypatch)
            # store answered, nothing matched -> empty (NOT unconfigured)
            out_e: dict = {}
            rows_e = sdk.tortoise_fts_query(
                None, kind="no_such_kind", read_status_out=out_e, limit=5)
            assert rows_e == []
            assert out_e["status"] == STATUS_EMPTY
            assert out_e["status"] != STATUS_UNCONFIGURED
        finally:
            sdk.close()
        sdk2 = sdk_factory()
        try:
            # store declared but will not open -> degraded (NOT unconfigured)
            _unopenable_store(monkeypatch, sdk2)
            out_u: dict = {}
            rows_u = sdk2.tortoise_fts_query(
                "alpha", read_status_out=out_u, limit=5)
            assert rows_u == []
            assert out_u["status"] == STATUS_DEGRADED
            assert out_u["status"] != STATUS_UNCONFIGURED
        finally:
            sdk2.close()
        # and the classifier still names the term for its one condition
        assert classify_read_status(
            configured=False, reached=False, hit_count=0,
            degraded=False) == STATUS_UNCONFIGURED

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

    def test_recall_state_unopenable_store_is_degraded(
            self, sdk_factory, monkeypatch):
        sdk = sdk_factory()
        try:
            _unopenable_store(monkeypatch, sdk)
            out: dict = {}
            rows = sdk.recall_state("alpha", read_status_out=out, limit=5)
            assert rows == []
            assert out["status"] == STATUS_DEGRADED
            assert out["status"] != STATUS_UNCONFIGURED
        finally:
            sdk.close()

    def test_recall_state_available_never_accompanies_zero_rows(
            self, sdk_factory, monkeypatch):
        """The status describes the read the caller RECEIVES: a rerank / floor
        that drops every candidate makes it `empty` (the store was reached and
        nothing matched), never `available`. Verified live before the fix:
        `recall_state(query=None, kind="statement", min_confidence=1.0)`
        returned 0 rows with `status='available'`."""
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            assert sdk.recall_state(None, kind="statement", limit=5)  # rows exist
            out: dict = {}
            rows = sdk.recall_state(
                None, kind="statement", min_confidence=1.0,
                read_status_out=out, limit=5)
            assert rows == []
            assert out["status"] == STATUS_EMPTY
            assert out["status"] != STATUS_AVAILABLE
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
