"""#8870: the version anchor must advance on the ENGINE THAT CAN SEE THE BUG.

The defect this file guards is a WRITE-ORDER defect, and the two engines
disagree about write order:

* the **server** engine applies the `SET` items of one clause LEFT TO RIGHT and
  each item observes the values the earlier items just wrote — so a clause that
  installs `contentHash` BEFORE the fields guarded on it makes every one of
  those guards read "unchanged" and quietly skip the write;
* the **embedded** engine does not, so the same clause behaves correctly there.

That asymmetry is why the bug survived: its pins ran on the embedded engine,
which cannot see it, and were green. A green bought from the engine that cannot
observe the failure is not evidence — the pins for both writers already existed
and both passed while the hash moved and `version` stayed at 1.

So these two tests run ONLY against a live FalkorDB, and skip (loudly, naming
the engine) when there is none. They are the pins that would have caught it.
"""
from __future__ import annotations

import os

import pytest

from tests import _live_utils
from tests.test_connector_source_version_5214 import GH_URL, _source, gh_issue_event
from tortoise.projection import FalkorProjection

URL = "https://example.test/8870-version-anchor"


def _live_proj() -> FalkorProjection:
    """A per-test graph on the live (server) engine — never the embedded one.

    Targets the PASSWORDED docker test lane (`docker_uri`, 6379), NOT
    `legacy_uri`: the legacy default resolves to the canonical fleet store, and
    minting a graph there is exactly what the #7357 guard exists to stop — it
    holds ~374 graphs against FalkorDB's default `CACHE_SIZE=25`, so one more
    test graph raises the eviction churn that aborts fleet writes with `graph
    was deleted or replaced`. The `test_projection.py::live_proj` fixture still
    uses the legacy default; that pre-existing divergence belongs on #7357, not
    here.
    """
    # Gate on the engine we are ABOUT to use, not on a fixed port: a lane may
    # point `TORTOISE_DB_URI` at a private container (tools/test_lane.py, #5084)
    # while the legacy port is closed, and skipping there told the operator to
    # "start a live FalkorDB" that was in fact up and addressed (#8870 review).
    uri = os.environ.get("TORTOISE_DB_URI")
    if not uri and not _live_utils.docker_reachable():
        pytest.skip(
            "#8870: this pin NEEDS the server engine — the embedded one does "
            "not apply SET items left-to-right, so it cannot observe the "
            "write-order defect at all. Start the live FalkorDB to run it."
        )
    proj = FalkorProjection.from_uri(
        uri or _live_utils.docker_uri("tortoise_test_8870"),
        graph_name=f"test_8870_{os.urandom(4).hex()}")
    proj.g.query("MATCH (n) DETACH DELETE n")
    return proj


def _props(proj: FalkorProjection, url: str) -> dict:
    rows = proj.g.query(
        f"MATCH (s:Source {{url:'{url}'}}) "
        "RETURN s.contentHash, s.version, s.title, s.updatedAt, s.validFrom, "
        "       s._searchText"
    ).result_set
    assert rows, f"no :Source at {url}"
    return dict(zip(
        ("contentHash", "version", "title", "updatedAt", "validFrom",
         "_searchText"), rows[0], strict=False))


def test_the_connector_writer_advances_the_ordinal_on_the_server_engine():
    """`_materialize_connector_source`: a content-changing repoll is a NEW
    version, so the ordinal beside the hash must move with it.

    FAILS IF the SET list writes `contentHash` before `version` — on the server
    engine the version's `s.contentHash <> $ch` guard then reads the hash the
    same clause just installed, is always false, and measures `version` 1 → 1
    while the hash moves. That is the exact shape #8870 was filed for.
    """
    proj = _live_proj()
    try:
        proj.apply(gh_issue_event())
        before = _source(proj, GH_URL)
        proj.apply(gh_issue_event(state="closed",
                                  closed_at="2026-07-11T00:00:00Z"))
        after = _source(proj, GH_URL)
    finally:
        proj.close()
    assert after[0] != before[0], "a state change is a new version"
    assert after[1] == before[1] + 1, (
        f"the ordinal must advance with the hash on the server engine: "
        f"{before[1]} -> {after[1]}"
    )


def test_upsert_source_advances_every_guarded_field_on_the_server_engine():
    """`_upsert_source`'s ON MATCH: EVERY field guarded on the hash diff must
    move with it — the ordinal, the title, both recorded instants, and
    `_searchText`.

    FAILS IF `s.contentHash` is written anywhere before the fields that test it:
    the hash lands, each guard reads "unchanged", and the whole version-anchor
    block is skipped while the hash silently moves on. `_searchText` is the LAST
    guard in the clause and therefore the one a partial re-ordering leaves
    behind, which is why it gets its own assertion rather than a mention.
    """
    proj = _live_proj()
    v1 = {
        "url": URL, "sourceKind": "document", "contentHash": "hash-v1",
        "title": "title-v1",
        "validFrom": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
    }
    v2 = dict(
        v1, contentHash="hash-v2", title="title-v2",
        validFrom="2026-02-02T00:00:00Z", updatedAt="2026-02-02T00:00:00Z")
    try:
        proj._upsert_source(dict(v1))
        first = _props(proj, URL)
        proj._upsert_source(dict(v2))
        second = _props(proj, URL)
    finally:
        proj.close()
    assert second["contentHash"] == "hash-v2", "the hash must move"
    assert second["version"] == first["version"] + 1, (
        f"the ordinal must advance with the hash: "
        f"{first['version']} -> {second['version']}")
    assert second["title"] == "title-v2", "the title must move with the hash"
    assert second["updatedAt"] == "2026-02-02T00:00:00Z", (
        "updatedAt must move with the hash")
    assert second["validFrom"] == "2026-02-02T00:00:00Z", (
        "validFrom must move with the hash")
    # `_searchText` is the LAST hash guard in the clause, so a partial
    # re-ordering that moves the hash write to just before it leaves this one
    # guard poisoned while every other assertion above still passes (#8870
    # review). `$st` derives from the title, so v2 must read `title-v2`.
    assert second["_searchText"] == "title-v2", (
        f"_searchText must move with the hash and derive from the NEW title, "
        f"got {second['_searchText']!r}")
