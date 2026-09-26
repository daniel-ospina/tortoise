"""#5199 — the READ half: can anything SEE the version note?

The owner asked, on #5199: *"are you making sure that the search endpoints/tools
in the SDK and MCP can read that note? because otherwise we added load to the
system without the wiring for the user to benefit from it."* Verified first, and
the answer was NO: `grep -rn sourceVersion tortoise/` returned the writer and
comments only. `TortoiseSDK.get_provenance_chain` walked the very hop the note
rides and returned the source's properties — which include the source's CURRENT
version — but never the note, so even that reader could not compare the two.

What this wiring ships: that reader reports the note, the source's current
version, and a derived ``currency`` **per link** — one row per
``(source, reference)`` hop, pinned so resolved links come before the
self-terminal fallback and annotated links before unannotated ones. That is a
**presentation** order, not a total one — the reader's docstring says so, and
tests below pin the shapes that could otherwise tie.

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

def _point_and_source(sdk, *, current, source="c.txt", point="pt_1"):
    proj = sdk._get_proj()
    proj.g.query(
        f"CREATE (p:Point {{id:'{point}', content:'a fact', pointKind:'fact', "
        f"status:'live', createdAt:'2024-01-01'}})"
    )
    proj.g.query(
        f"CREATE (s:Source {{url:'{source}', sourceKind:'corpus', "
        f"title:'{source}', contentHash:'{current}', ingestedAt:'2024-01-01'}})"
    )
    proj.g.query(
        f"MATCH (p:Point {{id:'{point}'}}), (s:Source {{url:'{source}'}}) "
        f"CREATE (p)-[:extractedFrom]->(s)"
    )
    return proj


def _annotate(proj, ref_url, version, source="c.txt"):
    proj.g.query(
        f"MERGE (d:Source {{url:'{ref_url}'}}) "
        f"WITH d MATCH (s:Source {{url:'{source}'}}) "
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


def test_provenance_chain_reports_every_link_of_a_multi_source_point():
    """REGRESSION (#5199 review P2). A Point read from two sources carries a note
    on EACH source's `references` link — so a reader that answered with ONE row
    hid a note, and a caller could not even see the per-link set. `MIN`/`LIMIT`
    caps here are not a simplification; they are data loss.

    FAILS IF the reader caps its result (e.g. re-adds `LIMIT 1`): this Point has
    two sources, each with its own annotated reference, and both must come back."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _point_and_source(sdk, current="h1", source="c1.txt", point="pt_1")
        proj.g.query(
            "CREATE (s:Source {url:'c2.txt', sourceKind:'corpus', title:'c2.txt', "
            "contentHash:'h2', ingestedAt:'2024-01-01'})"
        )
        proj.g.query(
            "MATCH (p:Point {id:'pt_1'}), (s:Source {url:'c2.txt'}) "
            "CREATE (p)-[:extractedFrom]->(s)"
        )
        _annotate(proj, "doc-1", "h1", source="c1.txt")
        _annotate(proj, "doc-2", "hX", source="c2.txt")
        rows = sdk.get_provenance_chain("pt_1")
        seen = {(r["source"]["url"], r["sourceVersion"]) for r in rows}
        assert seen == {("c1.txt", "h1"), ("c2.txt", "hX")}, (
            f"every link must be reported; got {sorted(seen)}"
        )
    finally:
        sdk.close()


def test_provenance_chain_reads_the_note_when_a_bare_link_came_first():
    """REGRESSION (#5199 review P1). `hosted_api` gives one Source a document
    derivation link AND external containment links, so a Source routinely has
    several `references` out-edges. When the reader answered with one arbitrary
    row, a bare containment link winning reported `unknown` for a chain whose
    note was right there — it said "no note" precisely when a note existed.

    FAILS IF the annotated reference does not come first: this graph inserts the
    UNANNOTATED link first and expects the note `h1` at the head of the rows."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _point_and_source(sdk, current="h2")
        proj.g.query("MERGE (d:Source {url:'bare-1'})")
        sdk.link_source_to_entity("c.txt", "bare-1", "Source")  # FIRST, no note
        _annotate(proj, "doc-1", "h1")                          # annotated, second
        rows = sdk.get_provenance_chain("pt_1")
        assert rows[0]["sourceVersion"] == "h1", (
            f"the note must be readable though a bare link was inserted first; "
            f"got {rows[0]['sourceVersion']!r} on {rows[0]['entity'].get('url')!r}"
        )
        assert [r["currency"] for r in rows] == ["stale", "unknown"], (
            f"the annotated hop comes first and the bare hop is still reported; "
            f"got {[(r['entity'].get('url'), r['currency']) for r in rows]}"
        )
    finally:
        sdk.close()


def test_provenance_chain_row_order_is_insertion_order_independent():
    """REGRESSION (#5199 review P2). Edge insertion order must not decide the
    order a caller sees: with several annotated references the sort keys must
    break the tie on VALUES, not on creation sequence."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _point_and_source(sdk, current="h2")
        _annotate(proj, "zzz", "hZ")
        _annotate(proj, "aaa", "hA")
        first = [r["entity"].get("url") for r in sdk.get_provenance_chain("pt_1")]

        sdk2 = TortoiseSDK(_tmp("test2.db"))
        try:
            proj2 = _point_and_source(sdk2, current="h2")
            _annotate(proj2, "aaa", "hA")   # reversed order
            _annotate(proj2, "zzz", "hZ")
            second = [r["entity"].get("url") for r in sdk2.get_provenance_chain("pt_1")]
            assert first == second == ["aaa", "zzz"], (
                f"insertion order decided the order: {first!r} vs {second!r}"
            )
        finally:
            sdk2.close()
    finally:
        sdk.close()


def test_provenance_chain_order_is_stable_for_event_only_targets():
    """REGRESSION (#5199 review P2). A legacy raw-Cypher Event carries neither
    `url` nor `id` (only `eventId`) — `link_source_to_event` and
    `link_source_to_legacy_event` both document that. So a node-key tie-break of
    `coalesce(ref.url, ref.id, '')` keys EVERY such candidate `''` and
    discriminates nothing, leaving insertion order in charge.

    FAILS IF the order keys ignore `eventId`: the two events below carry the SAME
    note value, so `coalesce(ref.url, ref.id, '')` and the note both tie and only
    `eventId` can break it — the two insertion orders would then disagree."""
    def _events(reverse: bool):
        sdk = TortoiseSDK(_tmp("ev.db"))
        proj = _point_and_source(sdk, current="h2")
        order = [("ev-zzz", "h1"), ("ev-aaa", "h1")]
        for eid, note in (reversed(order) if reverse else order):
            proj.g.query(
                f"MERGE (e:Event {{eventId:'{eid}'}}) "
                f"WITH e MATCH (s:Source {{url:'c.txt'}}) "
                f"CREATE (s)-[r:references]->(e) SET r.sourceVersion = $v",
                params={"v": note},
            )
        try:
            return [(r["entity"].get("eventId"), r["sourceVersion"])
                    for r in sdk.get_provenance_chain("pt_1")]
        finally:
            sdk.close()

    assert _events(False) == _events(True), (
        "eventId-only targets must not let insertion order decide the answer"
    )


def test_provenance_chain_orders_reference_less_sources_by_key():
    """REGRESSION (#5199 review P2). A source that references NOTHING yields a
    self-terminal fallback row whose `ref` is NULL, so EVERY `ref`-based order
    key evaluates to `''`. For a Point with several such sources the rows tied
    end to end and insertion order decided which document a caller sees first —
    the D10 legacy-document shape, reachable through the `api.add_document` sites
    in `tortoise/ingest.py`,
    which omits `source_url` at every site.

    FAILS IF the order keys ignore `src`: the two insertion orders below would
    then disagree about `rows[0]`, which is not a benign tie — the rows are
    different documents."""
    def _bare(reverse: bool):
        sdk = TortoiseSDK(_tmp("bare.db"))
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (p:Point {id:'pt_1', content:'a fact', pointKind:'fact', "
            "status:'live', createdAt:'2024-01-01'})"
        )
        for url, h in (("c1.txt", "h1"), ("c2.txt", "h2")):
            proj.g.query(
                f"CREATE (s:Source {{url:'{url}', sourceKind:'corpus', "
                f"title:'{url}', contentHash:'{h}', ingestedAt:'2024-01-01'}})"
            )
        order = ["c1.txt", "c2.txt"]
        for url in (reversed(order) if reverse else order):
            proj.g.query(
                f"MATCH (p:Point {{id:'pt_1'}}), (s:Source {{url:'{url}'}}) "
                f"CREATE (p)-[:extractedFrom]->(s)"
            )
        try:
            return [r["source"].get("url") for r in sdk.get_provenance_chain("pt_1")]
        finally:
            sdk.close()

    assert _bare(False) == _bare(True) == ["c1.txt", "c2.txt"], (
        "reference-less sources must be ordered by key, not by insertion order"
    )


def test_provenance_chain_separates_duplicate_sources_sharing_a_url():
    """REGRESSION (#5199 review P1). Two `:Source` nodes can share a `url` — it
    takes a legacy/raw-Cypher write path (`#5012`'s measured duplication is
    canonical identity across DIFFERENT raw urls), but nothing prevents it — and
    a pair of fallback rows from them agrees on the node key (`src.url`), so
    without a further key the pair is handed to the engine. They are different
    documents, so that is not a benign tie.

    BOTH the node and the edge creation order are reversed, so the
    insertion-order half of the assertion is real rather than vacuous, and the
    explicit expected order pins the key that does the work.

    FAILS IF `contentHash` is dropped from the order keys: `title` then decides,
    and since the titles deliberately sort AGAINST the hashes the answer becomes
    `["H2", "H1"]` instead of `["H1", "H2"]`. (The equality half only bites when
    the order stops being deterministic at all — with `title` still present both
    runs agree, so that half is a guard against a future regression, not the
    half this mutation trips.)"""
    def _dup(reverse: bool):
        sdk = TortoiseSDK(_tmp("dup.db"))
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (p:Point {id:'pt_1', content:'a fact', pointKind:'fact', "
            "status:'live', createdAt:'2024-01-01'})"
        )
        # Same url, different content — two distinct documents. The titles sort
        # AGAINST the hashes on purpose: if `contentHash` were dropped from the
        # order keys, `title` would then produce the opposite order and this test
        # would fail, so the assertion pins the hash key specifically.
        order = [("H1", "two"), ("H2", "one")]
        for h, title in (reversed(order) if reverse else order):
            proj.g.query(
                f"CREATE (s:Source {{url:'dup.txt', title:'{title}', "
                f"contentHash:'{h}', ingestedAt:'2024-01-01'}})"
            )
            proj.g.query(
                f"MATCH (p:Point {{id:'pt_1'}}), (s:Source {{contentHash:'{h}'}}) "
                f"CREATE (p)-[:extractedFrom]->(s)"
            )
        try:
            return [r["source"].get("contentHash") for r in sdk.get_provenance_chain("pt_1")]
        finally:
            sdk.close()

    assert _dup(False) == _dup(True) == ["H1", "H2"], (
        "duplicate :Source nodes must be separated by hash/title, not insertion order"
    )


def test_provenance_chain_separates_an_event_from_a_source_on_a_shared_key():
    """REGRESSION (#5199 review P1). Nothing keeps an `:Event`'s `eventId` and a
    `:Source`'s `url` in disjoint namespaces, so one source can reference both
    with the SAME string — the node key ties, and because the note is the
    source's own `contentHash` it is identical on every edge from that source, so
    the note key ties too. Only the target's LABEL separates them, and without it
    the two rows come back in engine order.

    FAILS IF `labels(...)` is dropped from the order keys."""
    def _collide(reverse: bool):
        sdk = TortoiseSDK(_tmp("collide.db"))
        proj = _point_and_source(sdk, current="h2")
        creates = {
            "Event": "MERGE (e:Event {eventId:'x'}) ",
            "Source": "MERGE (e:Source {url:'x'}) ",
        }
        order = ["Event", "Source"]
        for label in (reversed(order) if reverse else order):
            proj.g.query(
                creates[label]
                + "WITH e MATCH (s:Source {url:'c.txt'}) "
                + "CREATE (s)-[r:references]->(e) SET r.sourceVersion = 'note'"
            )
        try:
            return [r["labels"][0] for r in sdk.get_provenance_chain("pt_1")]
        finally:
            sdk.close()

    assert _collide(False) == _collide(True) == ["Event", "Source"], (
        "an :Event and a :Source sharing a key must be separated by label"
    )


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
