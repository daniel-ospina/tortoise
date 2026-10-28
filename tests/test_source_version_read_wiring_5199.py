"""#5199 — the READ half: can anything SEE the version note?

The owner asked, on #5199: *"are you making sure that the search endpoints/tools
in the SDK and MCP can read that note? because otherwise we added load to the
system without the wiring for the user to benefit from it."* Verified first, and
the answer was NO: `grep -rn sourceVersion tortoise/` returned the writer and
comments only. `TortoiseSDK.get_provenance_chain` walked the very hop the note
rides and returned the source's properties — which include the source's CURRENT
version — but never the note, so even that reader could not compare the two.

What this wiring ships: that reader reports the note, the source's current
version, and a derived ``linkCurrency`` **per link** — one row per
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

**#5581 update (the read-path item this module hands off to).** #5256 has
arrived, and the Point-level verdict is now shipped: the search hit reports the
**§4.6 AGGREGATE** of its own ``extractedFrom`` links (plus the sourced
disclosure) **inside the flag-gated ``provenance`` block**, so an unflagged row
is still byte-identical. ``test_search_hit_makes_no_version_claim`` keeps its
assertion untouched — a bare ``SearchResult`` must still carry no version claim
— but read it as the shape it is: the ban is on a **per-link** verdict (or an
unaggregated key) speaking for the Point, NOT on §4.6's aggregate. The aggregate
and its guard live in ``tests/test_source_currency_5581.py``.

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
    """The three states, and the one that must never be confused with fresh.

    FAILS IF any state is mis-mapped — most importantly if an empty input returns
    `current` instead of `unknown`."""
    assert currency_status("h1", "h1") == "current"
    assert currency_status("h1", "h2") == "stale"
    # ABSENT is unknown, never current — in both directions.
    assert currency_status("", "h1") == "unknown", "no note must not read as current"
    assert currency_status("h1", "") == "unknown", "no source version must not read as current"
    assert currency_status("", "") == "unknown"


def test_currency_is_derived_not_stored():
    """FAILS IF currency ever becomes a stored field on the edge: a stored verdict
    would put a ``linkCurrency``/``currency`` property on the link, so the note's
    own property set must be exactly ``sourceVersion`` (a stored status would need
    a backfill on every source edit and could disagree with the read)."""
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


# ── the deliberate cut ───────────────────────────────────────────────────────

def test_search_hit_makes_no_version_claim():
    """The search hit must NOT carry a Point-level `currency`, because one link
    cannot speak for a Point (ONTOLOGY §4.6: stale if ANY link is behind,
    current only when EVERY link is). Reporting one link's verdict as the
    Point's can say `current` for a stale Point.

    FAILS IF a version/currency claim is re-added to the hit — at the TOP level of
    `to_dict()` or inside its `provenance` block — without the §4.6 aggregation.
    Read this test's module docstring before "fixing" it.

    NOT superseded by #5581: the read-path item now ships a Point-level
    `provenance.currency`, but it is §4.6's AGGREGATE over the Point's own
    `extractedFrom` links (and flag-gated, so this bare hit is unchanged). The
    ban this test states is on a verdict DERIVED FROM ONE LINK — or an
    unaggregated key — speaking for the Point; that is still the
    false-current, and its guard is `tests/test_source_currency_5581.py`."""
    def _banned(d: dict) -> set:
        # A `currency` at the top level is the same per-link-speaks-for-the-Point
        # conflation, one nesting level up.
        return {"currency", "linkCurrency", "sourceVersion", "source_version",
                "linkRecordedVersion", "sourceCurrentVersion"} & set(d)

    d = SearchResult(id="pt_1", content="a fact", point_kind="fact").to_dict()
    assert not _banned(d), f"the hit must carry no version claim, got {sorted(d)}"
    assert "provenance" not in d

    # And again WITH provenance present — the block is emitted by a branch, so a
    # top-level re-add can hide behind it: this is the dict that has both levels.
    d = SearchResult(
        id="pt_1", content="a fact", point_kind="fact",
        source_ref="c.txt", captured_at="2024-01-01",
    ).to_dict()
    assert not _banned(d), (
        f"the hit's TOP level must stay free of version claims too, got {sorted(d)}"
    )
    assert set(d["provenance"]) == {"source", "captured_at"}, (
        f"the hit's provenance block must stay free of version claims, "
        f"got {sorted(d['provenance'])}"
    )
