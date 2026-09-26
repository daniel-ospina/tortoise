"""#5199 — the READ half: can a search result / SDK reader SEE the version note?

The owner asked, on #5199: *"are you making sure that the search endpoints/tools
in the SDK and MCP can read that note? because otherwise we added load to the
system without the wiring for the user to benefit from it."*

Before this wiring the answer was no: `grep -rn sourceVersion tortoise/` returned
the WRITER and comments only, and the closest reader (`get_provenance_chain`)
returned the source's properties — which include the source's CURRENT version —
but never the note, so nothing could compare the two.

The contract these tests pin:
- ``get_provenance_chain`` reports the note on the ``source -[references]-> entity``
  hop, the source's current version, and a derived ``currency``.
- A search hit carries the same pair in its ``provenance`` block (flag-gated).
- ``currency`` is ``current`` / ``stale`` / ``unknown``, and **``unknown`` is never
  rendered as ``current``** — an absent note must not read as fresh.
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

def _chain_graph(sdk, *, note, current):
    """A Point extracted from a source, where that source references a document
    carrying ``note``; the source's current hash is ``current``."""
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (p:Point {id:'pt_1', content:'a fact', pointKind:'fact', "
        "status:'live', createdAt:'2024-01-01'})"
    )
    proj.g.query(
        f"CREATE (s:Source {{url:'c.txt', sourceKind:'corpus', title:'c.txt', "
        f"contentHash:'{current}', ingestedAt:'2024-01-01'}})"
    )
    proj.g.query("CREATE (d:Source {url:'doc-1', title:'Doc'})")
    proj.g.query(
        "MATCH (p:Point {id:'pt_1'}), (s:Source {url:'c.txt'}) "
        "CREATE (p)-[:extractedFrom]->(s)"
    )
    if note is None:
        sdk.link_source_to_entity("c.txt", "doc-1", "Source")  # containment: no note
    else:
        proj.g.query(
            "MATCH (s:Source {url:'c.txt'}), (d:Source {url:'doc-1'}) "
            "CREATE (s)-[r:references]->(d) SET r.sourceVersion = $v",
            params={"v": note},
        )


def test_provenance_chain_exposes_the_note_and_says_stale():
    """The note names a version the source no longer holds → the caller is told
    the entity may be out of date, which is the whole point of writing it."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        _chain_graph(sdk, note="h1", current="h2")
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
        _chain_graph(sdk, note="h2", current="h2")
        assert sdk.get_provenance_chain("pt_1")[0]["currency"] == "current"
    finally:
        sdk.close()


def test_provenance_chain_says_unknown_when_the_link_carries_no_note():
    """The containment link deliberately carries no note. FAILS IF an unnoted
    link is reported as ``current`` — that is the false-current the note exists
    to expose, inverted."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        _chain_graph(sdk, note=None, current="h2")
        row = sdk.get_provenance_chain("pt_1")[0]
        assert row["sourceVersion"] == ""
        assert row["currency"] == "unknown", f"unnoted link read as {row['currency']!r}"
    finally:
        sdk.close()


# ── the search-hit surface ───────────────────────────────────────────────────

def test_search_result_provenance_block_carries_the_note_and_currency():
    """The wire shape a search endpoint / MCP tool returns: the remembered
    version, the source's version now, and the derived currency."""
    hit = SearchResult(
        id="pt_1", content="a fact", point_kind="fact",
        source_ref="c.txt", captured_at="2024-01-01",
        source_version="h1", source_current_version="h2",
    )
    prov = hit.to_dict()["provenance"]
    assert prov["sourceVersion"] == "h1"
    assert prov["sourceCurrentVersion"] == "h2"
    assert prov["currency"] == "stale"


def test_search_result_stays_silent_when_there_is_no_note():
    """A default hit (flag off) is unchanged: no note → no version keys at all,
    so nothing on the wire implies a currency that was never computed."""
    d = SearchResult(id="pt_1", content="a fact", point_kind="fact").to_dict()
    assert "provenance" not in d
    hit = SearchResult(id="pt_1", content="a fact", point_kind="fact",
                       source_ref="c.txt")
    prov = hit.to_dict()["provenance"]
    assert "currency" not in prov, "a hit with no note must not claim a currency"
    assert "sourceVersion" not in prov
