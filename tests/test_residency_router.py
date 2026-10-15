"""#7953 (§17.3) — the RESIDENCY ROUTER: a cache miss is not an insufficiency.

§12.4's sufficiency router routes on **answer depth** (*"does the evidence I hold
answer this?"* → escalate a tier). §17.3 adds a **second, distinct** router:
*"is this node in the known-hot set at all?"* → **fetch from the truth store**.
The two events are not the same event, and collapsing them either escalates
unnecessarily or — worse — **treats a cache miss as a negative finding**.

These tests pin the router's decision at the point where it is made, with the
states named canonically, and the two events separable there.

MUTATION PROOF — the guard each test exists to hold, and the edit that turns it
RED (the reverts and their verbatim pytest output are recorded in the PR body):

* **Collapse the two events** — make ``route_residency`` return
  ``escalate-tier`` for a ``not-hot`` marker, or move the sufficiency branch
  ahead of the residency branch → ``test_a_residency_miss_routes_to_the_record``
  and ``test_residency_outranks_sufficiency`` RED.
* **Route a down cache as "no evidence"** — drop the ``cache_reachable`` guard
  → ``test_a_down_cache_is_a_residency_miss_not_an_absence`` RED.
* **Report a partial miss as an absence** — drop ``apply_residency`` from the
  read path (or make ``mark_residency`` return ``hot`` for a partial
  declaration) → ``test_a_partial_miss_never_produces_a_negative_finding`` RED.
* **Drop the coordinate at the reader** — remove ``key=_residency_key`` from
  ``sdk._with_read_status`` → ``test_the_reader_reports_a_covered_key_as_hot``
  RED (the per-key branch stops firing, and a *covered* key is mis-routed).
* **Mint a route as a status term** — set a route name to ``"empty"`` →
  ``test_the_route_names_are_not_status_terms`` RED.
* **Coerce a container marker** — drop the `marker not in RESIDENCY_VERDICTS`
  guard in ``route_residency`` →
  ``test_a_presence_container_at_the_seam_is_refused_not_coerced`` RED.
* **Report an unassessed marker as resident** — restore the single final
  branch (``reason=REASON_RESIDENT`` for ``marker=None``) →
  ``test_an_unassessed_marker_is_reasoned_unassessed_not_resident`` RED.
* **Carry an unvalidated marker on the type** — drop the marker check in
  ``ResidencyRoute.__post_init__`` →
  ``test_the_decision_itself_refuses_an_unvalidated_marker`` RED.

Runs on the embedded lane (``TORTOISE_TEST_CARVE_OUT=1``) or a
``TORTOISE_DB_URI`` (``tests/conftest.py`` session gate).
"""
from __future__ import annotations

import dataclasses

import pytest

from tortoise import status_vocabulary
from tortoise.cache_projection import declared_key
from tortoise.residency import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    RESIDENCY_FIELD,
    RESIDENCY_FLAG_ENV,
    RESIDENCY_HOT,
    RESIDENCY_NOT_HOT,
    ResidencyDeclaration,
    declare_residency,
)
from tortoise.residency_router import (
    REASON_CACHE_UNREACHABLE,
    REASON_INSUFFICIENCY,
    REASON_RESIDENCY_MISS,
    REASON_RESIDENT,
    REASON_UNASSESSED,
    ROUTE_ESCALATE_TIER,
    ROUTE_FETCH_RECORD,
    ROUTE_SERVE,
    ROUTES,
    ResidencyRoute,
    route_for_coordinate,
    route_residency,
)


def _no_embedder(monkeypatch):
    """The dense leg cannot run — the #2573/#2898 degrade class."""
    from tortoise.embeddings import EmbeddingModel

    monkeypatch.setattr(
        EmbeddingModel, "get", classmethod(lambda cls, load_timeout=None: None))


class TestVocabulary:
    """The routes are a DIRECTIVE — never a status term (§17.3)."""

    def test_the_route_names_are_not_status_terms(self):
        terms = set(status_vocabulary.CLIENT_STATUS_TERMS)
        assert terms == {"available", "empty", "degraded", "unconfigured"}
        for route in ROUTES:
            assert route not in terms
        # and neither a route nor the marker is the other's vocabulary
        assert RESIDENCY_HOT not in ROUTES
        assert RESIDENCY_NOT_HOT not in ROUTES
        assert RESIDENCY_FIELD not in ROUTES

    def test_the_router_has_exactly_three_routes(self):
        assert ROUTES == ("serve", "fetch-record", "escalate-tier")
        assert ROUTE_SERVE == "serve"
        assert ROUTE_FETCH_RECORD == "fetch-record"
        assert ROUTE_ESCALATE_TIER == "escalate-tier"

    def test_a_route_is_never_a_container(self):
        """The S5 seam's presence set: a route must not collide with []/None/{}.

        Read off the DECISION, not only the ``ROUTES`` constant — the
        exact-names test already pins the constant, so this one asserts the
        property its name claims at the point a route is emitted. A route that
        ever became a container sentinel (the ``[]``/``None``/``{}`` collision
        S5 names) reddens here.
        """
        decisions = (
            route_residency(marker=RESIDENCY_HOT, sufficient=True),
            route_residency(marker=RESIDENCY_HOT, sufficient=False),
            route_residency(marker=RESIDENCY_NOT_HOT),
            route_residency(marker=None, cache_reachable=False),
        )
        for decision in decisions:
            route = decision.route
            assert isinstance(route, str) and route
            assert not isinstance(route, (list, dict, tuple, set))


class TestRouteResidency:
    """The decision, and the precedence that keeps the two events apart."""

    def test_a_residency_miss_routes_to_the_record(self):
        r = route_residency(marker=RESIDENCY_NOT_HOT)
        assert r.route == ROUTE_FETCH_RECORD
        assert r.reason == REASON_RESIDENCY_MISS
        assert r.fetches_record

    def test_an_insufficiency_escalates_a_tier(self):
        r = route_residency(marker=RESIDENCY_HOT, sufficient=False)
        assert r.route == ROUTE_ESCALATE_TIER
        assert r.reason == REASON_INSUFFICIENCY

    def test_a_resident_sufficient_read_serves(self):
        r = route_residency(marker=RESIDENCY_HOT, sufficient=True)
        assert r.route == ROUTE_SERVE
        assert r.reason == REASON_RESIDENT

    def test_a_resident_read_with_no_sufficiency_verdict_serves(self):
        """An unmeasured sufficiency is not an insufficiency — never escalate."""
        r = route_residency(marker=RESIDENCY_HOT, sufficient=None)
        assert r.route == ROUTE_SERVE

    def test_residency_outranks_sufficiency(self):
        """⭐ THE collapse guard: a miss is NOT an insufficiency.

        A node the cache cannot see makes every sufficiency verdict
        undecidable — escalating a tier would walk to a colder tier of the
        store that never held it. §17.3: the miss is repaired at the record.
        """
        r = route_residency(marker=RESIDENCY_NOT_HOT, sufficient=False)
        assert r.route == ROUTE_FETCH_RECORD
        assert r.route != ROUTE_ESCALATE_TIER

    def test_a_down_cache_is_a_residency_miss_not_an_absence(self):
        """The fourth branch (test-design S5): an outage is NOT "no evidence".

        A cache that is DOWN is reported as `degraded` and the marker stays
        absent (#7952) — so the router cannot read it as `hot`, and must not
        read it as an absence or as an insufficiency. It routes to the record.
        """
        r = route_residency(marker=None, cache_reachable=False, sufficient=False)
        assert r.route == ROUTE_FETCH_RECORD
        assert r.reason == REASON_CACHE_UNREACHABLE
        assert r.route != ROUTE_ESCALATE_TIER

    def test_accessibility_outranks_a_not_hot_verdict_in_reason_only(self):
        """Both are record fetches; the REASON names which one fired."""
        r = route_residency(
            marker=RESIDENCY_NOT_HOT, cache_reachable=False)
        assert r.route == ROUTE_FETCH_RECORD
        assert r.reason == REASON_CACHE_UNREACHABLE

    def test_an_unassessed_marker_is_not_a_claim_of_hot(self):
        """`None` means NOT ASSESSED — the decision records that, never `hot`."""
        r = route_residency(marker=None, sufficient=True)
        assert r.route == ROUTE_SERVE
        assert r.marker is None

    def test_an_unassessed_marker_defers_to_sufficiency(self):
        """With the marker off, behaviour is §12.4's — the byte-identical default."""
        r = route_residency(marker=None, sufficient=False)
        assert r.route == ROUTE_ESCALATE_TIER

    def test_an_unassessed_marker_is_reasoned_unassessed_not_resident(self):
        """NOT ASSESSED carries its OWN reason — never a ``resident`` claim.

        ``reason`` names WHICH EVENT FIRED. A marker that never ran is not a
        positive residency verdict, so a consumer keyed on ``reason`` must not
        read one — ``.marker is None`` preserves the distinction, and the
        reason must state it too. The ROUTE stays ``serve``.

        Non-vacuity: restoring the single final branch (``reason=
        REASON_RESIDENT`` for ``marker=None``) reddens this test.
        """
        r = route_residency(marker=None, sufficient=True)
        assert r.route == ROUTE_SERVE
        assert r.reason == REASON_UNASSESSED
        assert r.reason != REASON_RESIDENT
        assert r.marker is None

    def test_the_decision_itself_refuses_an_unvalidated_marker(self):
        """The TYPE enforces the marker rule, not only the factory.

        ``ResidencyRoute`` is in ``__all__``, so a decision constructed
        directly must not be able to carry a verdict the module refuses. The
        two canonical verdicts and the NOT-ASSESSED sentinel stay legal.

        Non-vacuity: dropping the marker check in ``__post_init__`` lets
        ``marker="cold"`` (and a container) construct successfully, and this
        test goes RED.
        """
        for bad in ("cold", [], {}, ()):
            with pytest.raises(ValueError):
                ResidencyRoute(
                    route=ROUTE_SERVE, reason=REASON_RESIDENT, marker=bad)
        for good in (RESIDENCY_HOT, RESIDENCY_NOT_HOT, None):
            assert ResidencyRoute(
                route=ROUTE_SERVE, reason=REASON_RESIDENT,
                marker=good).marker == good

    def test_an_unknown_verdict_is_refused_not_guessed(self):
        """A typo'd verdict must fail loud — a wrong route is a wrong answer."""
        with pytest.raises(ValueError):
            route_residency(marker="cold")

    @pytest.mark.parametrize("marker", [[], {}, ()])
    def test_a_presence_container_at_the_seam_is_refused_not_coerced(self, marker):
        """S5's presence set: `[]`/`{}` are VALUES, never "no marker".

        Test-design `#7967` S5 names the seam failure as "`not-hot` colliding
        with `[]` / `None` / `{}`", and fixes the boundary at five values —
        `not-hot`, `[]`, `None`, `{}`, and a real answer — "All five must be
        distinguished at S5's seam." Only `None` is the NOT-ASSESSED sentinel;
        a container silently coerced (to `None`, or to a truthy `not-hot`)
        would make a miss indistinguishable from a resident read, so it must
        fail loud rather than take a branch.

        Non-vacuity: deleting the `marker not in RESIDENCY_VERDICTS` guard in
        `route_residency` lets `marker=[]` fall through to `serve` instead of
        raising, and this test goes RED.
        """
        with pytest.raises(ValueError):
            route_residency(marker=marker)

    def test_the_coordinate_rides_the_decision(self):
        """The fetch-from-record repair must know WHAT to fetch."""
        r = route_residency(
            marker=RESIDENCY_NOT_HOT, coordinate="Point:p-1")
        assert r.coordinate == "Point:p-1"

    def test_the_decision_records_what_it_read(self):
        r = route_residency(
            marker=RESIDENCY_HOT, sufficient=False, coordinate="Point:p-1")
        assert r.marker == RESIDENCY_HOT
        assert r.sufficiency is False
        assert isinstance(r, ResidencyRoute)
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.route = ROUTE_SERVE  # frozen: a decision is not editable


class TestRouteForCoordinate:
    """The per-key entry — the bridge to #7971's ``declared_key``."""

    def test_a_complete_declaration_routes_nothing_and_serves(self):
        """The §2.0 posture: the cache is complete, so the marker cannot fire."""
        d = ResidencyDeclaration(coverage=COVERAGE_COMPLETE)
        r = route_for_coordinate(d, label="Point", key="p-1", hits=0)
        assert r.route == ROUTE_SERVE
        assert r.reason == REASON_RESIDENT
        assert r.coordinate == declared_key("Point", "p-1")

    def test_a_partial_declaration_routes_an_unwritten_key_to_the_record(self):
        """⭐ The deliverable: an UNWRITTEN key is a miss, never an absence."""
        d = ResidencyDeclaration(
            coverage=COVERAGE_PARTIAL, hot_keys=frozenset({declared_key("Point", "p-1")}))
        r = route_for_coordinate(d, label="Point", key="p-2", hits=0)
        assert r.route == ROUTE_FETCH_RECORD
        assert r.reason == REASON_RESIDENCY_MISS

    def test_a_covered_key_that_returned_nothing_is_an_in_store_finding(self):
        """The other side of the guard — this is what the COORDINATE buys.

        Without the coordinate a partial zero-hit read is always `not-hot`;
        with it, a key the declaration COVERS is a genuine finding of absence
        *within* the store, so no record fetch is warranted.
        """
        d = ResidencyDeclaration(
            coverage=COVERAGE_PARTIAL, hot_keys=frozenset({declared_key("Point", "p-1")}))
        r = route_for_coordinate(d, label="Point", key="p-1", hits=0)
        assert r.route == ROUTE_SERVE
        assert r.reason == REASON_RESIDENT

    def test_a_partial_hit_is_hot_and_routes_on_sufficiency(self):
        d = ResidencyDeclaration(coverage=COVERAGE_PARTIAL)
        r = route_for_coordinate(
            d, label="Point", key="p-2", hits=3, sufficient=False)
        assert r.route == ROUTE_ESCALATE_TIER

    def test_the_scope_is_part_of_the_coordinate(self):
        """#7971's scoped coordinate — a scoped write does not cover the bare key."""
        d = ResidencyDeclaration(
            coverage=COVERAGE_PARTIAL,
            hot_keys=frozenset({declared_key("Point", "p-1", scope="g_a")}))
        r = route_for_coordinate(d, label="Point", key="p-1", hits=0)
        assert r.route == ROUTE_FETCH_RECORD
        r_scoped = route_for_coordinate(
            d, label="Point", key="p-1", hits=0, scope="g_a")
        assert r_scoped.route == ROUTE_SERVE

    def test_an_empty_key_is_refused(self):
        d = ResidencyDeclaration(coverage=COVERAGE_PARTIAL)
        with pytest.raises(ValueError):
            route_for_coordinate(d, label="Point", key="", hits=0)

    def test_a_down_cache_over_a_coordinate_still_routes_to_the_record(self):
        d = ResidencyDeclaration(
            coverage=COVERAGE_PARTIAL, hot_keys=frozenset({declared_key("Point", "p-1")}))
        r = route_for_coordinate(
            d, label="Point", key="p-1", hits=0, cache_reachable=False)
        assert r.route == ROUTE_FETCH_RECORD
        assert r.reason == REASON_CACHE_UNREACHABLE


class TestReadPathEndToEnd:
    """The reader passes the COORDINATE, and the miss is never an absence."""

    def _declare_partial(self, sdk, *keys):
        declare_residency(
            sdk._get_proj().g,
            ResidencyDeclaration(
                coverage=COVERAGE_PARTIAL, hot_keys=frozenset(keys) or None))

    def test_the_reader_reports_a_covered_key_as_hot(
            self, sdk_factory, monkeypatch):
        """⭐ The reader change, pinned: the coordinate reaches `mark_residency`.

        With a PARTIAL declaration that COVERS the coordinate, a zero-hit read
        of that key is `hot` (a finding WITHIN the store). Without the key —
        today's set-level polarity — the same read is `not-hot`. So the key is
        what makes the distinction observable, and removing it reds this test.
        """
        monkeypatch.setenv(RESIDENCY_FLAG_ENV, "1")
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            coord = declared_key("Point", "p-1")
            self._declare_partial(sdk, coord)
            out: dict = {}
            rows = sdk.tortoise_fts_query(
                None, kind="no_such_kind", read_status_out=out, limit=5,
                _residency_key=coord)
            assert rows == []
            assert out[RESIDENCY_FIELD] == RESIDENCY_HOT
            # the key was COVERED, so the zero-hit read is a real in-store
            # finding — `empty` may stand, and no record fetch is warranted
            assert route_residency(
                marker=out[RESIDENCY_FIELD]).route == ROUTE_SERVE
        finally:
            sdk.close()

    def test_a_partial_miss_never_produces_a_negative_finding(
            self, sdk_factory, monkeypatch):
        """⭐ §17.3's load-bearing case, end to end through the router."""
        monkeypatch.setenv(RESIDENCY_FLAG_ENV, "1")
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            self._declare_partial(sdk, declared_key("Point", "p-other"))
            out: dict = {}
            rows = sdk.tortoise_fts_query(
                None, kind="no_such_kind", read_status_out=out, limit=5,
                _residency_key=declared_key("Point", "p-1"))
            assert rows == []
            assert out[RESIDENCY_FIELD] == RESIDENCY_NOT_HOT
            # never an absence: the status carries the impairment, and the
            # router fetches the record rather than reporting "no such node"
            assert out["status"] != "empty"
            assert out["status"] == "degraded"
            assert route_residency(
                marker=out[RESIDENCY_FIELD]).route == ROUTE_FETCH_RECORD
        finally:
            sdk.close()

    def test_the_coordinate_is_optional_and_a_set_read_is_unchanged(
            self, sdk_factory, monkeypatch):
        """A set read names no node, so its polarity stays set-level."""
        monkeypatch.setenv(RESIDENCY_FLAG_ENV, "1")
        _no_embedder(monkeypatch)
        sdk = sdk_factory()
        try:
            self._declare_partial(sdk, declared_key("Point", "p-1"))
            out: dict = {}
            sdk.tortoise_fts_query(
                None, kind="no_such_kind", read_status_out=out, limit=5)
            assert out[RESIDENCY_FIELD] == RESIDENCY_NOT_HOT
        finally:
            sdk.close()
