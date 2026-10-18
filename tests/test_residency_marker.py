"""#7952 (§17.2 / §17.3) — the COMPLETENESS MARKER.

Postgres is the record and FalkorDB is a DERIVED HOT CACHE, partial by design.
Without a marker, *"this node is not in the cache"* and *"no such node"* are the
same answer — an empty result — so the cache silently answers *"nothing"* to a
question it cannot see. These tests pin the marker and, more importantly, the
ONE consequence §17.3 draws from it: **a `not-hot` read is never reported as
`empty`.**

MUTATION PROOF — the guard has been shown to MOVE (the revert and its verbatim
pytest output are recorded in the PR body):

* **Report the miss as an absence** — make ``mark_residency`` return
  ``hot`` for a partial declaration, or drop ``apply_residency`` from
  ``_with_read_status`` → ``test_partial_zero_hits_is_not_hot``,
  ``test_not_hot_never_stands_in_for_empty`` and both end-to-end
  ``..._is_degraded_never_empty`` tests RED.
* **Mint the verdict as a term** — add ``not-hot`` to
  ``status_vocabulary.CLIENT_STATUS_TERMS`` → the vocabulary tests RED.
* **Guess on an unreachable store** — emit ``not-hot`` on the
  ``_get_proj()`` / probe-failure paths → the marker would claim a residency
  miss for an outage; the down-store test (below) pins the field's ABSENCE
  there.

These run on the embedded lane (``TORTOISE_TEST_CARVE_OUT=1``) or a
``TORTOISE_DB_URI`` (``tests/conftest.py`` session gate).
"""
from __future__ import annotations

import asyncio

import pytest

from tortoise import status_vocabulary
from tortoise.read_status import READ_STATUSES
from tortoise.residency import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    DECLARATION_META_KEY,
    RESIDENCY_FIELD,
    RESIDENCY_FLAG_ENV,
    RESIDENCY_HOT,
    RESIDENCY_NOT_HOT,
    RESIDENCY_VERDICTS,
    ResidencyDeclaration,
    apply_residency,
    clear_residency,
    combine_residency,
    declare_residency,
    mark_residency,
    read_declaration,
    residency_enabled,
)


def _no_embedder(monkeypatch):
    """The dense leg cannot run — the #2573/#2898 degrade class."""
    from tortoise.embeddings import EmbeddingModel

    monkeypatch.setattr(
        EmbeddingModel, "get", classmethod(lambda cls, load_timeout=None: None))


class _FakeGraph:
    """A projection's ``g`` stub for the declaration reader."""

    def __init__(self, rows=None, *, raises=None):
        self.rows = rows
        self.raises = raises
        self.queries: list[tuple[str, dict]] = []
        self.timeouts: list = []

    def query(self, cypher, params=None, timeout=None):
        self.queries.append((cypher, params or {}))
        self.timeouts.append(timeout)

        class _R:
            result_set = self.rows if self.raises is None else []

        if self.raises is not None:
            raise self.raises
        return _R()


class TestVocabulary:
    """The marker is NOT a fifth status term (§17.3)."""

    def test_the_four_recorded_terms_still_stand_alone(self):
        assert status_vocabulary.CLIENT_STATUS_TERMS == (
            "available", "empty", "degraded", "unconfigured")
        assert READ_STATUSES == status_vocabulary.CLIENT_STATUS_TERMS
        assert len(READ_STATUSES) == 4

    def test_not_hot_is_not_a_status_term(self):
        assert RESIDENCY_NOT_HOT not in status_vocabulary.CLIENT_STATUS_TERMS
        assert RESIDENCY_NOT_HOT not in READ_STATUSES
        assert RESIDENCY_HOT not in READ_STATUSES

    def test_the_verdicts_are_exactly_the_recorded_spelling(self):
        assert RESIDENCY_NOT_HOT == "not-hot"
        assert RESIDENCY_VERDICTS == (RESIDENCY_HOT, RESIDENCY_NOT_HOT)

    def test_the_field_is_its_own_key_and_not_the_status_key(self):
        assert RESIDENCY_FIELD == "residency"
        assert RESIDENCY_FIELD != "status"

    def test_a_verdict_is_never_a_container(self):
        """The presence set: `not-hot` must not collide with `[]`/`None`/`{}`.

        This is §17.3's S5 seam stated as a type fact — the router distinguishes
        *"not in the cache"* from *"the evidence does not answer this"* on it.
        """
        for verdict in RESIDENCY_VERDICTS:
            assert verdict not in ([], None, {}, ())
            assert isinstance(verdict, str) and verdict


class TestFlag:
    def test_the_marker_is_off_by_default(self, monkeypatch):
        monkeypatch.delenv(RESIDENCY_FLAG_ENV, raising=False)
        assert residency_enabled() is False

    @pytest.mark.parametrize("value", ["0", "", "off", "no", "false", "maybe"])
    def test_falsy_and_garbage_values_leave_it_off(self, monkeypatch, value):
        monkeypatch.setenv(RESIDENCY_FLAG_ENV, value)
        assert residency_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_the_truthy_contract_turns_it_on(self, monkeypatch, value):
        monkeypatch.setenv(RESIDENCY_FLAG_ENV, value)
        assert residency_enabled() is True


class TestDeclaration:
    def test_an_unknown_coverage_is_refused_not_defaulted(self):
        with pytest.raises(ValueError):
            ResidencyDeclaration(coverage="partially")

    def test_a_complete_declaration_covers_everything(self):
        d = ResidencyDeclaration()
        assert d.covers(None) is True
        assert d.covers("p1") is True

    def test_an_unenumerated_partial_declaration_covers_nothing(self):
        d = ResidencyDeclaration(coverage=COVERAGE_PARTIAL)
        assert d.covers("p1") is False
        assert d.covers(None) is False

    def test_an_enumerated_partial_declaration_covers_exactly_its_keys(self):
        d = ResidencyDeclaration(coverage=COVERAGE_PARTIAL,
                                 hot_keys=frozenset({"p1"}))
        assert d.covers("p1") is True
        assert d.covers("p2") is False


class TestMarkResidency:
    def test_complete_store_marks_hot(self):
        assert mark_residency(ResidencyDeclaration(), hits=0) == RESIDENCY_HOT

    def test_partial_zero_hits_is_not_hot(self):
        """⭐ THE load-bearing case: the miss is a DIRECTIVE, not an absence."""
        d = ResidencyDeclaration(coverage=COVERAGE_PARTIAL)
        assert mark_residency(d, hits=0) == RESIDENCY_NOT_HOT

    def test_partial_with_hits_is_hot(self):
        d = ResidencyDeclaration(coverage=COVERAGE_PARTIAL)
        assert mark_residency(d, hits=3) == RESIDENCY_HOT

    def test_partial_and_a_covered_key_is_hot(self):
        """Inside the hot set a miss IS a finding — `empty` may stand."""
        d = ResidencyDeclaration(coverage=COVERAGE_PARTIAL,
                                 hot_keys=frozenset({"p1"}))
        assert mark_residency(d, hits=0, key="p1") == RESIDENCY_HOT

    def test_partial_and_an_uncovered_key_is_not_hot(self):
        d = ResidencyDeclaration(coverage=COVERAGE_PARTIAL,
                                 hot_keys=frozenset({"p1"}))
        assert mark_residency(d, hits=0, key="p2") == RESIDENCY_NOT_HOT

    def test_a_hit_outranks_an_uncovered_key(self):
        d = ResidencyDeclaration(coverage=COVERAGE_PARTIAL,
                                 hot_keys=frozenset({"p1"}))
        assert mark_residency(d, hits=1, key="p2") == RESIDENCY_HOT


class TestApplyResidency:
    """§17.3's consequence: a residency miss never surfaces as an absence."""

    def test_not_hot_never_stands_in_for_empty(self):
        assert apply_residency("empty", RESIDENCY_NOT_HOT) == "degraded"

    def test_a_consulted_record_makes_empty_a_real_finding(self):
        assert apply_residency(
            "empty", RESIDENCY_NOT_HOT, record_consulted=True) == "empty"

    def test_a_hot_read_is_untouched(self):
        assert apply_residency("empty", RESIDENCY_HOT) == "empty"

    @pytest.mark.parametrize("status", ["available", "degraded", "unconfigured"])
    def test_nothing_else_moves(self, status):
        assert apply_residency(status, RESIDENCY_NOT_HOT) == status
        assert apply_residency(status, RESIDENCY_HOT) == status

    def test_apply_needs_a_verdict(self):
        assert apply_residency("empty", None) == "empty"


class TestReadDeclaration:
    """The known-hot set is DATA, and the reader fails SAFE (never false-fires)."""

    def test_a_missing_declaration_is_complete(self):
        d = read_declaration(_FakeGraph(rows=[]))
        assert d.coverage == COVERAGE_COMPLETE
        assert d.source is None
        assert mark_residency(d, hits=0) == RESIDENCY_HOT

    def test_a_partial_declaration_is_read(self):
        d = read_declaration(_FakeGraph(rows=[["partial", ["p1", "p2"]]]))
        assert d.coverage == COVERAGE_PARTIAL
        assert d.hot_keys == frozenset({"p1", "p2"})
        assert d.source is not None

    def test_a_partial_declaration_without_keys_is_read(self):
        d = read_declaration(_FakeGraph(rows=[["partial", None]]))
        assert d.coverage == COVERAGE_PARTIAL
        assert d.hot_keys is None

    def test_a_malformed_coverage_stays_complete_and_names_itself(self):
        d = read_declaration(_FakeGraph(rows=[["nonsense", None]]))
        assert d.coverage == COVERAGE_COMPLETE
        assert d.source is not None and "malformed" in d.source

    def test_an_unreadable_store_stays_complete_and_names_itself(self):
        d = read_declaration(_FakeGraph(raises=RuntimeError("wedged")))
        assert d.coverage == COVERAGE_COMPLETE
        assert d.source is not None and "unreadable" in d.source
        assert mark_residency(d, hits=0) == RESIDENCY_HOT

    def test_the_declaration_query_is_bounded(self):
        g = _FakeGraph(rows=[])
        read_declaration(g, timeout_ms=250)
        assert g.queries and g.queries[0][1] == {"key": DECLARATION_META_KEY}
        # the bound is the property this test NAMES: a wedged store must not
        # stall the annotated read, so the timeout is forwarded — not just the
        # params. (Without this assertion, deleting `timeout=int(timeout_ms)`
        # from `read_declaration` left the whole suite green.)
        assert g.timeouts == [250]

    def test_the_default_read_path_also_passes_a_bound(self):
        g = _FakeGraph(rows=[])
        read_declaration(g)
        assert g.timeouts == [500]
        assert all(isinstance(t, int) and t > 0 for t in g.timeouts)


class TestCombineResidency:
    """One composite read, one directive — the legs' markers coalesce."""

    def test_nothing_assessed_is_no_marker(self):
        assert combine_residency(None, None) is None
        assert combine_residency() is None

    def test_one_not_hot_marks_the_composite_not_hot(self):
        assert combine_residency(RESIDENCY_HOT, RESIDENCY_NOT_HOT) == \
            RESIDENCY_NOT_HOT
        assert combine_residency(None, RESIDENCY_NOT_HOT) == RESIDENCY_NOT_HOT

    def test_a_hot_leg_with_no_verdict_is_hot(self):
        assert combine_residency(RESIDENCY_HOT, None) == RESIDENCY_HOT


class TestReadPathEndToEnd:
    """The marker on the SHIPPED read path, over a real store."""

    def _declare(self, sdk, *, partial: bool):
        declare_residency(
            sdk._get_proj().g,
            ResidencyDeclaration(coverage=(
                COVERAGE_PARTIAL if partial else COVERAGE_COMPLETE)))

    def test_a_partial_store_marks_a_nonexistent_node_not_hot(
            self, sdk_factory, monkeypatch):
        monkeypatch.setenv(RESIDENCY_FLAG_ENV, "1")
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            self._declare(sdk, partial=True)
            out: dict = {}
            rows = sdk.tortoise_fts_query(
                None, kind="no_such_kind", read_status_out=out, limit=5)
            assert rows == []
            assert out[RESIDENCY_FIELD] == RESIDENCY_NOT_HOT
            # ⭐ an empty hit list may NOT be read as "no such node"
            assert out["status"] != "empty"
            assert out["status"] == "degraded"
        finally:
            sdk.close()

    def test_a_complete_store_marks_a_nonexistent_node_hot(
            self, sdk_factory, monkeypatch):
        """The §2.0 default: the cache is complete, so a miss IS a finding."""
        monkeypatch.setenv(RESIDENCY_FLAG_ENV, "1")
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            self._declare(sdk, partial=False)
            out: dict = {}
            rows = sdk.tortoise_fts_query(
                None, kind="no_such_kind", read_status_out=out, limit=5)
            assert rows == []
            assert out[RESIDENCY_FIELD] == RESIDENCY_HOT
            assert out["status"] == "empty"
        finally:
            sdk.close()

    def test_a_partial_store_that_answers_is_hot(self, sdk_factory, monkeypatch):
        monkeypatch.setenv(RESIDENCY_FLAG_ENV, "1")
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            self._declare(sdk, partial=True)
            out: dict = {}
            rows = sdk.tortoise_fts_query(
                None, kind="statement", read_status_out=out, limit=5)
            assert rows
            assert out[RESIDENCY_FIELD] == RESIDENCY_HOT
        finally:
            sdk.close()

    def test_the_marker_is_absent_with_the_flag_off(
            self, sdk_factory, monkeypatch):
        monkeypatch.delenv(RESIDENCY_FLAG_ENV, raising=False)
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            self._declare(sdk, partial=True)
            out: dict = {}
            rows = sdk.tortoise_fts_query(
                None, kind="no_such_kind", read_status_out=out, limit=5)
            assert rows == []
            # off means off: the declaration is never even read, and the
            # pre-#7952 status behaviour is untouched
            assert RESIDENCY_FIELD not in out
            assert out["status"] == "empty"
        finally:
            sdk.close()

    def test_a_store_that_will_not_open_is_not_marked_not_hot(
            self, sdk_factory, monkeypatch):
        """An outage is NOT a residency miss (§17.3's S5 failure mode).

        A cache that is DOWN must not be reported as *"outside the known-hot
        set"*: the marker is a directive, and `degraded` is what names the
        impairment. The field stays ABSENT so the two are separable.
        """
        monkeypatch.setenv(RESIDENCY_FLAG_ENV, "1")
        sdk = sdk_factory()
        try:
            def _health_check_failed():
                raise RuntimeError(
                    "DB health check failed on open (server/production mode).")

            # `_get_proj()` raising IS what a configured-but-down store looks
            # like on the read path (the `_unopenable_store` seam in
            # tests/test_read_status.py).
            monkeypatch.setattr(sdk, "_get_proj", _health_check_failed)
            out: dict = {}
            rows = sdk.tortoise_fts_query("alpha", read_status_out=out, limit=5)
            assert rows == []
            assert out["status"] == "degraded"
            assert RESIDENCY_FIELD not in out
        finally:
            sdk.close()

    def test_clear_returns_to_the_complete_default(
            self, sdk_factory, monkeypatch):
        monkeypatch.setenv(RESIDENCY_FLAG_ENV, "1")
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            self._declare(sdk, partial=True)
            assert clear_residency(sdk._get_proj().g) is True
            assert clear_residency(sdk._get_proj().g) is False
            out: dict = {}
            sdk.tortoise_fts_query(
                None, kind="no_such_kind", read_status_out=out, limit=5)
            assert out[RESIDENCY_FIELD] == RESIDENCY_HOT
        finally:
            sdk.close()

    def test_a_re_declaration_replaces_the_known_hot_set(self, sdk_factory):
        """A re-declaration REPLACES the set — it does not merge into it.

        `SET n += $props` is additive: a later keyless declaration that omitted
        the enumeration kept the previous `hot_keys`, so `read_declaration`
        reported coverage the current declaration no longer claimed and a
        covered-key miss surfaced as an absence (§17.2).
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            declare_residency(g, ResidencyDeclaration(
                coverage=COVERAGE_PARTIAL, hot_keys=frozenset({"p1", "p2"})))
            assert read_declaration(g).hot_keys == frozenset({"p1", "p2"})
            # shrink to an unenumerated partial: the old keys must be GONE
            declare_residency(g, ResidencyDeclaration(coverage=COVERAGE_PARTIAL))
            assert read_declaration(g).hot_keys is None
            # a later enumeration must not resurrect the dropped ones either
            declare_residency(g, ResidencyDeclaration(
                coverage=COVERAGE_PARTIAL, hot_keys=frozenset({"p3"})))
            assert read_declaration(g).hot_keys == frozenset({"p3"})
            # and a COMPLETE re-declaration must drop the enumeration too
            declare_residency(g, ResidencyDeclaration(coverage=COVERAGE_COMPLETE))
            read = read_declaration(g)
            assert read.coverage == COVERAGE_COMPLETE
            assert read.hot_keys is None
        finally:
            sdk.close()

    def test_recall_state_carries_the_composite_marker(
            self, sdk_factory, monkeypatch):
        """The composite read coalesces its legs' markers — one directive."""
        monkeypatch.setenv(RESIDENCY_FLAG_ENV, "1")
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            self._declare(sdk, partial=True)
            out: dict = {}
            rows = sdk.recall_state(
                None, kind="no_such_kind", read_status_out=out, limit=5)
            assert rows == []
            assert out[RESIDENCY_FIELD] == RESIDENCY_NOT_HOT
            # §17.3 at the composite: an empty result is not established
            assert out["status"] != "empty"
        finally:
            sdk.close()


class TestHostedSearchRoute:
    """The emitted field on the shipped GET /v1/search response."""

    def _patch_route(self, monkeypatch):
        import tortoise.hosted_api as hosted_api

        class _FakeSDK:
            def tortoise_fts_query(self, q, limit=10, read_status_out=None, **_ignored):
                if read_status_out is not None:
                    read_status_out["status"] = "empty"
                    read_status_out[RESIDENCY_FIELD] = RESIDENCY_NOT_HOT
                return [{"id": "p1", "content": "alpha", "pointKind": "statement"}]

            def close(self):
                pass

        monkeypatch.setattr(hosted_api, "_data_sdk", lambda org: _FakeSDK())
        monkeypatch.setattr(hosted_api, "_require_scope",
                            lambda org, scope, name: None)
        return hosted_api

    def _flags(self, monkeypatch, *, status: bool, residency: bool):
        for name, on in (("TORTOISE_READ_STATUS", status),
                         (RESIDENCY_FLAG_ENV, residency)):
            if on:
                monkeypatch.setenv(name, "1")
            else:
                monkeypatch.delenv(name, raising=False)

    def test_both_fields_are_absent_by_default(self, monkeypatch):
        hosted_api = self._patch_route(monkeypatch)
        self._flags(monkeypatch, status=False, residency=False)
        body = asyncio.run(hosted_api.search(q="alpha", limit=5, org={}))
        assert body == {"results": [{"id": "p1", "content": "alpha",
                                     "kind": "statement"}], "count": 1}

    def test_the_residency_field_rides_the_response_when_enabled(
            self, monkeypatch):
        hosted_api = self._patch_route(monkeypatch)
        self._flags(monkeypatch, status=False, residency=True)
        body = asyncio.run(hosted_api.search(q="alpha", limit=5, org={}))
        assert body[RESIDENCY_FIELD] == RESIDENCY_NOT_HOT
        # its OWN flag: turning the marker on does not turn the status field on
        assert "status" not in body

    def test_the_status_flag_alone_does_not_emit_the_marker(self, monkeypatch):
        hosted_api = self._patch_route(monkeypatch)
        self._flags(monkeypatch, status=True, residency=False)
        body = asyncio.run(hosted_api.search(q="alpha", limit=5, org={}))
        assert body["status"] == "empty"
        assert RESIDENCY_FIELD not in body

    def test_a_down_store_still_names_the_impairment_with_only_residency_on(
            self, monkeypatch):
        """§17.2: a residency-only deployment must not serve a bare empty.

        On the outage path the known-hot set cannot be read, so `residency` is
        absent (never a guessed `not-hot`). But the response must still name
        the impairment term: `{"results": [], "count": 0}` alone is
        byte-identical to a genuine empty result, which is exactly what the
        marker exists to prevent.
        """
        import tortoise.hosted_api as hosted_api

        class _DownSDK:
            def tortoise_fts_query(self, q, limit=10, read_status_out=None, **_ignored):
                if read_status_out is not None:
                    # the store is down: the SDK sets `degraded` and the
                    # declaration is never read, so no residency key.
                    read_status_out["status"] = "degraded"
                return []

            def close(self):
                pass

        monkeypatch.setattr(hosted_api, "_data_sdk", lambda org: _DownSDK())
        monkeypatch.setattr(hosted_api, "_require_scope",
                            lambda org, scope, name: None)
        self._flags(monkeypatch, status=False, residency=True)
        body = asyncio.run(hosted_api.search(q="alpha", limit=5, org={}))
        assert body["status"] == "degraded"
        assert RESIDENCY_FIELD not in body

    def test_the_down_store_impairment_is_absent_when_both_flags_are_off(
            self, monkeypatch):
        """Off means off: the outage response is byte-identical to before."""
        import tortoise.hosted_api as hosted_api

        class _DownSDK:
            def tortoise_fts_query(self, q, limit=10, read_status_out=None, **_ignored):
                if read_status_out is not None:
                    read_status_out["status"] = "degraded"
                return []

            def close(self):
                pass

        monkeypatch.setattr(hosted_api, "_data_sdk", lambda org: _DownSDK())
        monkeypatch.setattr(hosted_api, "_require_scope",
                            lambda org, scope, name: None)
        self._flags(monkeypatch, status=False, residency=False)
        body = asyncio.run(hosted_api.search(q="alpha", limit=5, org={}))
        assert body == {"results": [], "count": 0}

    def test_both_fields_ride_when_both_flags_are_on(self, monkeypatch):
        hosted_api = self._patch_route(monkeypatch)
        self._flags(monkeypatch, status=True, residency=True)
        body = asyncio.run(hosted_api.search(q="alpha", limit=5, org={}))
        assert body["status"] == "empty"
        assert body[RESIDENCY_FIELD] == RESIDENCY_NOT_HOT
