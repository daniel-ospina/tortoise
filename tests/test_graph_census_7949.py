"""#8444 census, for `#7949` E1 and E3 — non-vacuous, fail-closed, report-only.

⚠️ **THIS FILE LOST 15 OF ITS 19 TESTS (#4282, 2026-10-27), and the loss is MEASURED,
not assumed.** Every one of them ran the census over a real capture, and the fixture
entered through `TortoiseSDK.capture_session` — one of the 26 dead public SDK methods
the owner authorised deleting. The rule this PR applies to such a file is
*"fixture-only with no surviving SDK entry point → excise the fixture"*, and the last
clause is the one worth checking, so it was checked. Four probes, each against the
lane's FalkorDB, replaying the deleted door's surviving primitives in the documented
order:

1. `_extract_session_v2` alone writes the payload — a Point, an Object, an
   `aboutObject` edge and an `extractedFrom` edge — but mints no `:Session` and
   stamps no `eventId`: `snapshot_session` returns 0 points, the census 0 objects;
2. adding `_write_session_and_turns` lands the `:Session` node and the turn rows, and
   still 0 points;
3. adding the deterministic `sessionCaptured` Event and
   `_materialize_session_source` lands both nodes, and still 0 points;
4. adding `_stamp_capture_provenance` (returns `True`; the Point then carries
   `eventId`) STILL reads 0 points, because the census's session scope walks
   `(src:Source {sessionId})-[:references]->(e:Event …)` and that link's source key
   was derived by the deleted method (`_index_resolve_corpus_root`, itself orphaned
   now).

Reconstructing the rest means re-implementing the door's ORDERING contract — the
#8501 "materialize the Source BEFORE extraction" rule among it — inside a test
fixture, i.e. testing the fixture's imitation of a path nothing else exercises. That
is the instrument-false-negative class this PR deletes, so the tests stay out and the
loss is recorded with its boundary instead of asserted: the only door that survives
whole is the hosted async `_capture_session_impl` (a `SessionRequest` + org + capture
slot + request state), and rebuilding this fixture on it is filed as its own work.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.eval.write_path import runner

CONV = [
    {"role": "user", "content": "I think the auth dead-end is the top issue. "
                                "We decided to ship serve --http first."},
    {"role": "assistant", "content": "Agreed. Evidence suggests the website "
                                     "config is the root cause."},
    {"role": "user", "content": "ok"},
]




def _point_ids(snap: dict) -> list[str]:
    """The session's memory layer: claims AND reified operator Points."""
    return [p["point_id"]
            for p in snap.get("points", []) + snap.get("operator_nodes", [])]












def test_the_census_uses_the_same_instrument_as_the_e1_probe():
    """FAILS IF the census grows a SECOND, subtly different E1 predicate.

    The measured E1 figure this bundle reports comes from
    `docs/evidence/7949-capstone/probes/e1_e2_probe.py::THE_PREFIX`. A census
    copy of `the\\s+\\S` (the pre-fix spelling) is a strict SUBSET of it — the
    probe matches a name of exactly `"the "` and the census did not — so the
    two numbers would be produced by two instruments under one name, which is
    exactly what E1's claim to authority rules out. Pinned on BOTH surfaces:
    the pattern/flags, and the classification over the boundary names.
    """
    import importlib.util as ilu

    probe_path = (Path(__file__).resolve().parent.parent / "docs" / "evidence"
                  / "7949-capstone" / "probes" / "e1_e2_probe.py")
    spec = ilu.spec_from_file_location("e1_e2_probe_census_pin", str(probe_path))
    assert spec and spec.loader, probe_path
    probe = ilu.module_from_spec(spec)
    spec.loader.exec_module(probe)

    assert runner._THE_DEFINITE_DESCRIPTION.pattern == probe.THE_PREFIX.pattern
    assert runner._THE_DEFINITE_DESCRIPTION.flags == probe.THE_PREFIX.flags

    boundary = ["the rollout policy", "The rollout policy", "THE rollout",
                "the", "the ", "the\npolicy", "policy on the rollout",
                "theme", "", " the rollout"]
    assert [bool(runner._THE_DEFINITE_DESCRIPTION.match(n)) for n in boundary] == \
        [bool(probe.THE_PREFIX.match(n)) for n in boundary], boundary












def test_a_graph_error_is_reported_never_zeroed():
    """FAILS IF `census_or_error` swallows a graph failure into zeros — a
    measured empty session and an unreadable graph must not be the same
    artifact (`#8501`'s defect class one level up).

    Deliberately requests NO `captured` fixture: this is a pure-unit leg, and a
    real capture would pay for a measurement it never reads.
    """

    class _Boom:
        def query(self, *_a, **_k):
            raise RuntimeError("socket closed")

    class _Sdk:
        def _get_proj(self):
            class _P:
                g = _Boom()
            return _P()

    census = runner.census_or_error(_Sdk(), "session-x", ["p1"])
    assert "error" in census and "socket closed" in census["error"], census
    assert "objects" not in census

    aggregate = runner.aggregate_graph_census({"session-x": census})
    assert aggregate["sessions_errored"] == ["session-x"]
    assert aggregate["objects"]["total"] == 0
    assert aggregate["objects"]["the_share"] is None  # never 0.0






def test_the_aggregate_is_a_ratio_of_sums_not_a_mean_of_shares():
    """FAILS IF the aggregate averages per-session shares — a one-Object
    session would then weigh as much as a twenty-Object one, and `#7949` E1's
    corpus-level share would be wrong in exactly the direction that flatters a
    small session."""
    per_session = {
        "a": {"objects": {"total": 1, "the_prefixed": 1, "the_share": 1.0,
                          "with_derivation_link": 0},
              "derivation_edges": {"extractedFrom": {"total": 1, "anchored": 1},
                                   "references": {"total": 1, "anchored": 1,
                                                  "containment_excluded": 2,
                                                  "identity_excluded": 0}}},
        "b": {"objects": {"total": 3, "the_prefixed": 0, "the_share": 0.0,
                          "with_derivation_link": 0},
              "derivation_edges": {"extractedFrom": {"total": 0, "anchored": 0},
                                   "references": {"total": 1, "anchored": 0,
                                                  "containment_excluded": 1,
                                                  "identity_excluded": 4}}},
    }
    aggregate = runner.aggregate_graph_census(per_session)
    assert aggregate["objects"]["the_share"] == pytest.approx(0.25)  # not 0.5
    assert aggregate["derivation_edges"]["extractedFrom"] == {
        "total": 1, "anchored": 1}
    # The EXCLUDED populations are summed too, not silently dropped: the
    # receipt carries this aggregate as the corpus-level figure, so a reader of
    # it must be able to see every edge the census declined to judge.
    assert aggregate["derivation_edges"]["references"] == {
        "total": 2, "anchored": 1,
        "containment_excluded": 3, "identity_excluded": 4}
    assert aggregate["sessions_with_objects"] == 2
    assert aggregate["sessions_errored"] == []








# ── the retracted claim in the epic's own E1/E2 bundle ─────────────────────

_BUNDLE = (Path(__file__).resolve().parent.parent
           / "docs" / "evidence" / "7949-capstone" / "E1-E2.md")


def test_the_bundle_retracts_the_claim_that_a_graph_census_adds_nothing():
    """FAILS IF the retracted wording comes back, or the counterexample is lost.

    The bundle's limit 2 said a graph census "would catch no defect this one
    misses". `#8501` is that defect: every `extractedFrom` link carried a NULL
    `sourceVersion` (0/113) — an EDGE property, which the Layer-1 payload
    census structurally cannot see. The retraction is pinned here so the claim
    cannot be restored silently by a later reader who only reads the limits
    list, and the `#8501` numbers must stay quoted with it.
    """
    text = " ".join(_BUNDLE.read_text(encoding="utf-8").split())
    # Quoting a retracted sentence INSIDE its retraction is the convention; the
    # claim being live again is the failure. So the sentence may appear exactly
    # once, and only as a quotation named as retracted.
    retracted = "would add a docker dependency and catch no defect this one misses"
    assert text.count(retracted) == 1, (
        "the retracted graph-census claim appears more than once — one of them "
        "is no longer a quotation")
    at = text.index(retracted)
    window = text[max(0, at - 300):at + 300]
    assert "was a measured falsehood, retracted here" in window, (
        "the graph-census claim is live again: it is not named as retracted "
        "where it is quoted")
    assert "0/113" in text, "the retraction must carry the measured counterexample"
    assert "graph_census" in text, (
        "the bundle must name the instrument that closes the gap (#8444)")
