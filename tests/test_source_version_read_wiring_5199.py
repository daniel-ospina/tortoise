"""#5199 — the READ half: can anything SEE the version note?

The owner asked, on #5199: *"are you making sure that the search endpoints/tools
in the SDK and MCP can read that note? because otherwise we added load to the
system without the wiring for the user to benefit from it."* Verified first, and
the answer was NO: `grep -rn sourceVersion tortoise/` returned the writer and
comments only. `TortoiseSDK.get_provenance_chain` walked the very hop the note
rides and returned the source's properties — which include the source's CURRENT
version — but never the note, so even that reader could not compare the two.

What this wiring ships: that reader now reports the note, the source's version,
and a derived ``currency``.

What it deliberately does NOT ship is a currency on the SEARCH hit, and the
reason is worth keeping: a hit is a **Point**, and a Point's currency is an
AGGREGATE over its links (ONTOLOGY §4.6 — stale if ANY link is behind, current
only when EVERY link is). Deriving one Point-level verdict from a single link
reports ``current`` for a Point another link makes stale, i.e. exactly the
false-current the note exists to prevent. The Point's own link
(``extractedFrom.sourceVersion``) is also written by #5256, which is stacked on
this branch and not yet merged — so a search-side verdict assembled here could
not be exercised in production at all. It goes to the read-path item on #5038
with #5256's arrival. ``test_search_hit_makes_no_version_claim`` pins that cut,
so re-adding it has to face §4.6.

Every test below names the input that makes it FAIL.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import SearchResult, currency_status


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_test_"), name)


# ── the primitive ────────────────────────────────────────────────────────────

def test_currency_status_is_a_read_of_the_pair():
    """The three states, and the one that must never be confused with fresh."""
    assert currency_status("h1", "h1") == "current"
    assert currency_status("h1", "h2") == "stale"
    # ABSENT is unknown, never current — in both directions.
    assert currency_status("", "h1") == "unknown", "no note must not read as current"
    assert currency_status("h1", "") == "unknown", "no source version must not read as current"
    assert currency_status("", "") == "unknown"


def test_currency_is_derived_not_stored():
    """FAILS IF currency ever becomes a stored field on the edge: the note's own
    property set must be exactly ``sourceVersion`` (a stored status would need a
    backfill on every source edit and could disagree with the read)."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'c.txt', sourceKind:'corpus', title:'c.txt', "
            "contentHash:'h1', ingestedAt:'2024-01-01'})"
        )
        proj.g.query("CREATE (d:Source {url:'doc-1', title:'Doc'})")
        sdk.link_source_to_entity("c.txt", "doc-1", "Document")
        rows = proj.g.query(
            "MATCH (:Source {url:'c.txt'})-[r:references]->(:Source {url:'doc-1'}) "
            "RETURN keys(r)"
        ).result_set
        assert rows[0][0] == ["sourceVersion"], (
            f"the derivation link must carry ONLY the note, got {rows[0][0]!r}"
        )
    finally:
        sdk.close()


# ── the SDK reader ───────────────────────────────────────────────────────────

def _point_and_source(sdk, *, current):
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (p:Point {id:'pt_1', content:'a fact', pointKind:'fact', "
        "status:'live', createdAt:'2024-01-01'})"
    )
    proj.g.query(
        f"CREATE (s:Source {{url:'c.txt', sourceKind:'corpus', title:'c.txt', "
        f"contentHash:'{current}', ingestedAt:'2024-01-01'}})"
    )
    proj.g.query(
        "MATCH (p:Point {id:'pt_1'}), (s:Source {url:'c.txt'}) "
        "CREATE (p)-[:extractedFrom]->(s)"
    )
    return proj


def _annotate(proj, ref_url, version):
    proj.g.query(
        f"MERGE (d:Source {{url:'{ref_url}'}}) "
        f"WITH d MATCH (s:Source {{url:'c.txt'}}) "
        f"CREATE (s)-[r:references]->(d) SET r.sourceVersion = $v",
        params={"v": version},
    )


def test_provenance_chain_exposes_the_note_and_says_stale():
    """The note names a version the source no longer holds → the caller is told
    the entity may be out of date, which is the whole point of writing it."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _point_and_source(sdk, current="h2")
        _annotate(proj, "doc-1", "h1")
        chain = sdk.get_provenance_chain("pt_1")
        assert chain, "the provenance chain must resolve"
        row = chain[0]
        assert row["sourceVersion"] == "h1"
        assert row["sourceCurrentVersion"] == "h2"
        assert row["currency"] == "stale"
    finally:
        sdk.close()


def test_provenance_chain_says_current_when_the_note_matches():
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _point_and_source(sdk, current="h2")
        _annotate(proj, "doc-1", "h2")
        assert sdk.get_provenance_chain("pt_1")[0]["currency"] == "current"
    finally:
        sdk.close()


def test_provenance_chain_says_unknown_when_the_link_carries_no_note():
    """A containment link deliberately carries no note. FAILS IF an unnoted link
    is reported as ``current`` — that is the false-current the note exists to
    expose, inverted."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _point_and_source(sdk, current="h2")
        proj.g.query("MERGE (d:Source {url:'doc-1'})")
        sdk.link_source_to_entity("c.txt", "doc-1", "Source")  # containment: no note
        row = sdk.get_provenance_chain("pt_1")[0]
        assert row["sourceVersion"] == ""
        assert row["currency"] == "unknown", f"unnoted link read as {row['currency']!r}"
    finally:
        sdk.close()


def test_provenance_chain_reads_the_note_when_a_bare_link_came_first():
    """REGRESSION (#5199 review P1). `hosted_api` gives one Source a document
    derivation link AND external containment links, so a Source routinely has
    several `references` out-edges. With `ORDER BY ref IS NULL LIMIT 1` the
    winner was plan order, and a bare containment link winning reported
    `unknown` for a chain whose note was right there — the reader answered "no
    note" precisely when a note existed.

    FAILS IF the annotated reference is not preferred: this graph inserts the
    UNANNOTATED link first and expects the note `h1` anyway."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _point_and_source(sdk, current="h2")
        proj.g.query("MERGE (d:Source {url:'bare-1'})")
        sdk.link_source_to_entity("c.txt", "bare-1", "Source")  # FIRST, no note
        _annotate(proj, "doc-1", "h1")                          # annotated, second
        row = sdk.get_provenance_chain("pt_1")[0]
        assert row["sourceVersion"] == "h1", (
            f"the note must be readable though a bare link was inserted first; "
            f"got {row['sourceVersion']!r} on {row['entity'].get('url')!r}"
        )
        assert row["currency"] == "stale"
    finally:
        sdk.close()


def test_provenance_chain_pick_is_insertion_order_independent():
    """REGRESSION (#5199 review P1). Two annotated references must not let edge
    insertion order decide what the caller sees: flip the creation order and the
    same entity must come back."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _point_and_source(sdk, current="h2")
        _annotate(proj, "zzz", "hZ")
        _annotate(proj, "aaa", "hA")
        first = sdk.get_provenance_chain("pt_1")[0]["entity"].get("url")

        sdk2 = TortoiseSDK(_tmp("test2.db"))
        try:
            proj2 = _point_and_source(sdk2, current="h2")
            _annotate(proj2, "aaa", "hA")   # reversed order
            _annotate(proj2, "zzz", "hZ")
            second = sdk2.get_provenance_chain("pt_1")[0]["entity"].get("url")
            assert first == second, f"plan order decided the pick: {first!r} vs {second!r}"
        finally:
            sdk2.close()
    finally:
        sdk.close()


# ── the deliberate cut ───────────────────────────────────────────────────────

def test_search_hit_makes_no_version_claim():
    """The search hit must NOT carry a Point-level `currency`, because one link
    cannot speak for a Point (ONTOLOGY §4.6: stale if ANY link is behind,
    current only when EVERY link is). Reporting one link's verdict as the
    Point's can say `current` for a stale Point.

    FAILS IF a version/currency claim is re-added to the hit's `provenance`
    block without the §4.6 aggregation — read this test's module docstring
    before "fixing" it."""
    d = SearchResult(id="pt_1", content="a fact", point_kind="fact").to_dict()
    assert "provenance" not in d
    prov = SearchResult(
        id="pt_1", content="a fact", point_kind="fact",
        source_ref="c.txt", captured_at="2024-01-01",
    ).to_dict()["provenance"]
    assert set(prov) == {"source", "captured_at"}, (
        f"the hit's provenance block must stay free of version claims, got {sorted(prov)}"
    )
