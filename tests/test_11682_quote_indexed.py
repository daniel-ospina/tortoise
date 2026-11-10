"""#11682 — a Point's VERBATIM ``quote`` is part of the lexical search surface.

THE DEFECT (reproduced before the fix, docker lane): the ``Point`` FULLTEXT
index was declared over ``content`` + ``search_keys`` only, while the write
paths persist the point's original wording in ``quote``
(``projection/entities.py``; read back by the SDK). A query phrased the way the
user actually said it therefore matched nothing — the paraphrase is the only
thing the lexical leg could see::

    Point FULLTEXT fields: ['content', 'search_keys']
    query 'ledger' (in content) -> fts reason=ok           count=1
    query 'QUOKKA' (only in quote) -> fts reason=empty_results count=0

The same omission was in every mirror of that field list: the in-process BM25
leg's corpus read and the degraded snapshot lane both joined ``content`` ∪
``search_keys``, so the two lexical stacks agreed with each other and both
disagreed with the data.

THE FIX. ``sparse.POINT_INDEX_TEXT_FIELDS`` is now the ONE declaration
(``content``, ``search_keys``, ``quote``); the index DDL, the BM25 corpus and
the snapshot lane all read it. A graph carrying the pre-#11682 two-field index
is migrated (the legacy branch's schema probe now asks about the WHOLE declared
set instead of only ``search_keys`` — #5444's trap, one widening later).

WHAT THIS PINS (each revert must red a named test):
  * drop ``quote`` from ``POINT_INDEX_TEXT_FIELDS``
        -> ``test_the_point_index_field_set_carries_the_quote`` and the live
           ``…findable_by_the_fts_leg`` test red;
  * revert the legacy probe to ``"search_keys" not in kinds``
        -> ``test_a_pre_11682_two_field_index_is_upgraded_to_carry_the_quote``
           reds (the index stays two-field, the already-written point stays
           un-findable, and the probe stays not-current forever);
  * revert the BM25 corpus read to content ∪ search_keys
        -> ``test_the_bm25_mirror_finds_the_quote_too`` reds;
  * drop the ``quote`` from ``_SNAPSHOT_QUERY``
        -> the ``"quote" in _SNAPSHOT_QUERY`` assertion reds, and so does
           ``test_every_declared_field_is_selected_by_the_snapshot_mirror``,
           which walks the WHOLE declared set against that query;
  * drop ``"quote": r[7]`` from the snapshot's ROW MAPPING (or shift it)
        -> the ``row["quote"] == VERBATIM`` / ``row["has_answer"] is False``
           assertions in `test_the_bm25_mirror_finds_the_quote_too` red;

BOTH DIRECTIONS are asserted, and the negative controls are explicit: the
content-only word is still found (the leg was not narrowed), a word in NEITHER
field is still absent (the union was not widened into "return everything"), and
a point with no ``quote`` composes to byte-identical index text (an unquoted
graph degrades to pre-#11682 behaviour, never to empty).

LANE. The live arms need FalkorDB's fulltext index (``TORTOISE_DB_URI``); they
skip VISIBLY without it, like every docker-lane module.
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001

from tests._live_utils import _skip_unless_live_uri
from tortoise.search_engine import (
    reset_circuit_breakers,
    run_bm25_query,
    run_fts_query,
)
from tortoise.sdk import TortoiseSDK

# The utterance as it was actually said, the lossy paraphrase the fact is
# stored as, and a decoy so a "returns something" leg cannot pass for a hit.
VERBATIM = "we adopted the QUOKKA migration plan for the ledger"
PARAPHRASE = "The team chose the ledger migration plan"
DECOY = "an unrelated note about coffee machines"
QUOTE_ONLY_TERM = "QUOKKA"     # only in the quote
CONTENT_TERM = "ledger"        # in the paraphrase
ABSENT_TERM = "zzquartzitezz"  # in neither field


# ── the declaration (hermetic — no DB) ────────────────────────────────────


def test_the_point_index_field_set_carries_the_quote() -> None:
    """ONE declaration, read by the index DDL and required by the probe."""
    from tortoise.projection import (
        _FULLTEXT_INDEX_LABEL_FIELDS,
        FalkorProjection,
    )
    from tortoise.sparse import POINT_INDEX_TEXT_FIELDS

    assert POINT_INDEX_TEXT_FIELDS == ("content", "search_keys", "quote"), (
        "the Point lexical field set must carry the verbatim quote (#11682) — "
        "without it a quote-only phrase is unreachable by the FTS/BM25 legs")
    assert dict(_FULLTEXT_INDEX_LABEL_FIELDS)["Point"] == \
        list(POINT_INDEX_TEXT_FIELDS), (
            "the index DDL broke away from the ONE field-set declaration")
    assert ("Point", "quote", "FULLTEXT") in \
        FalkorProjection._REQUIRED_FULLTEXT_INDEXES, (
        "a graph with a two-field Point index must read NOT current, or the "
        "drop→recreate migration never runs and the quote stays unindexed")
    assert FalkorProjection._POINT_FTS_TARGET_FIELDS == POINT_INDEX_TEXT_FIELDS, (
        "the migration's TARGET must be the declared set it migrates to")


def test_point_index_text_includes_the_quote_and_degrades_without_it() -> None:
    """The join every mirror calls: quote in, absent quote = unchanged text."""
    from tortoise.sparse import point_index_text

    assert point_index_text(
        {"content": "c", "search_keys": ["a", "b"], "quote": "the verbatim w"}) \
        == "c a b the verbatim w"
    # NEGATIVE CONTROL: an unquoted point's index text is byte-identical to
    # the pre-#11682 text — an unbound graph degrades to current behaviour.
    assert point_index_text({"content": "c", "search_keys": "a b"}) == "c a b"
    assert point_index_text({"content": "", "search_keys": None,
                             "quote": None}) == ""


def test_every_declared_field_is_selected_by_the_snapshot_mirror() -> None:
    """The constant governs the JOIN; each mirror selects its own columns.

    A field added to ``POINT_INDEX_TEXT_FIELDS`` reaches the DDL and both
    joins, but NOT ``_SNAPSHOT_QUERY``'s column list — that had to be edited by
    hand for ``quote`` (#11706 review round 1, F3). This walk is what makes a
    FOURTH field red instead of the mirror silently leaving the declared set
    and feeding ``point_index_text`` a dict that never selected it.
    """
    from tortoise import fallback_snapshot as fs
    from tortoise.sparse import POINT_INDEX_TEXT_FIELDS

    for field in POINT_INDEX_TEXT_FIELDS:
        assert (f"n.{field}" in fs._SNAPSHOT_QUERY
                or f"coalesce(n.{field}" in fs._SNAPSHOT_QUERY), (
                    f"the snapshot mirror does not select {field!r} — the "
                    "declared Point lexical field set and this mirror drifted")


# ── live (docker lane) ────────────────────────────────────────────────────


@pytest.fixture()
def sdk():
    """A fresh docker graph per test (unique name → no cross-test bleed)."""
    _skip_unless_live_uri()
    reset_circuit_breakers()
    s = TortoiseSDK(graph_name=f"test_11682_quote_{uuid.uuid4().hex[:8]}")
    try:
        yield s
    finally:
        reset_circuit_breakers()
        s.close()


def _quoted_point(sdk) -> str:
    """A point whose ONLY record of the original wording is its ``quote``."""
    pid = sdk.create_point(
        "statement", PARAPHRASE, quote=VERBATIM, source_turn_id=1)["id"]
    sdk.create_point("statement", DECOY)
    return pid


def _point_fulltext_fields(graph) -> set[str] | None:
    """The Point FULLTEXT field set, read from the row that carries FULLTEXT.

    Row-per-label is what this engine returns today, but taking the LAST Point
    row would read another index's fields if the engine ever split them; the
    FULLTEXT row is the same rule ``_schema_is_current`` and the migration
    probe use.
    """
    for row in graph.query("CALL db.indexes()").result_set:
        if not row or row[0] != "Point":
            continue
        kinds = row[2] or {}
        fields = {f for f, ks in kinds.items()
                  if "FULLTEXT" in {str(k).upper() for k in (ks or ())}}
        if fields:
            return fields
    return None


def test_a_verbatim_quote_only_phrase_is_findable_by_the_fts_leg(sdk) -> None:
    """THE defect, both directions, on the graph-side lexical leg.

    FAIL VALUE (measured on the pre-fix tree): the quote-only query returned
    ``fts reason=empty_results count=0`` and the point was absent.
    """
    pid = _quoted_point(sdk)
    graph = sdk._get_proj().g

    trace: list[dict] = []
    quote_hits = [i for i, _ in run_fts_query(
        graph, QUOTE_ONLY_TERM, entity_type="point", leg_trace=trace)]
    assert pid in quote_hits, (
        "a query phrased the way it was SAID must find the point that quotes "
        f"it; got {quote_hits}, trace={trace}")

    # CONTROL 1 — the paraphrase is still indexed (the leg was not narrowed).
    assert pid in [i for i, _ in run_fts_query(
        graph, CONTENT_TERM, entity_type="point")], (
            "the content word must stay findable — #11682 ADDS a field")

    # CONTROL 2 — a term in NEITHER field is still absent (the union did not
    # become "return everything").
    assert [i for i, _ in run_fts_query(
        graph, ABSENT_TERM, entity_type="point")] == [], (
            "a term held by no indexed field must not match")


def test_the_bm25_mirror_finds_the_quote_too(sdk) -> None:
    """The in-process BM25 leg must not disagree with the graph's index.

    Its module docstring claims ``content`` ∪ ``search_keys`` is "the SAME
    fields each served label's own FTS index is declared on, byte-for-byte".
    Once the index carries ``quote``, a leg that does not is a parity break —
    the exact drift ``POINT_INDEX_TEXT_FIELDS`` exists to prevent.
    """
    pid = _quoted_point(sdk)

    bm25_hits = [i for i, _ in run_bm25_query(
        sdk._get_proj().g, QUOTE_ONLY_TERM, entity_type="point", limit=10,
        excluded_statuses=())]
    assert pid in bm25_hits, (
        f"the BM25 corpus text must include the quote; got {bm25_hits}")

    # NEGATIVE CONTROL — the BM25 leg did not become "match anything".
    assert [i for i, _ in run_bm25_query(
        sdk._get_proj().g, ABSENT_TERM, entity_type="point", limit=10,
        excluded_statuses=())] == []

    # The degraded snapshot lane reads the SAME set: its query must select the
    # quote column, or the mirror silently falls back to content ∪ search_keys.
    from tortoise import fallback_snapshot as fs
    assert "quote" in fs._SNAPSHOT_QUERY, (
        "the snapshot mirror must carry the verbatim quote (#11682)")

    # …and the ROW MAPPING must consume it, not just select it. A wrong column
    # index is silent: `quote` and `has_answer` would swap keys, the query-string
    # assertion above would still pass, and the mirror's corpus text would be
    # built from the wrong field (#11706 review round 1, F5).
    snap = fs.build_snapshot(sdk._get_proj())
    assert snap is not None, "the snapshot did not build on a two-point graph"
    row = next(p for p in snap["points"] if p["id"] == pid)
    assert row["quote"] == VERBATIM, (
        "the snapshot's row mapping did not carry the verbatim quote; "
        f"row={row}")
    assert row["has_answer"] is False, (
        "the row mapping crossed its columns — `has_answer` reads the quote "
        f"column; row={row}")


def test_a_pre_11682_two_field_index_is_upgraded_to_carry_the_quote(sdk) -> None:
    """The migration, measured on an ALREADY-WRITTEN point.

    A graph built before this change carries ``Point(content, search_keys)``.
    The point is written FIRST, then the index is downgraded to that exact
    pre-fix shape: the quote is un-indexed (so the query misses — the legacy
    state), and one sweep must widen the index and make the EXISTING node
    findable without rewriting it.

    FAIL VALUE: reverting the legacy probe to a ``search_keys``-only question
    leaves the index two-field, so the sweep never migrates, the query below
    never matches and ``_schema_is_current`` never returns True.
    """
    pid = _quoted_point(sdk)
    proj = sdk._get_proj()
    g = proj.g

    dropped = False
    for proc in ("db.idx.fulltext.drop", "db.idx.fulltext.dropIndex"):
        try:
            g.query(f"CALL {proc}('Point')")
            dropped = True
            break
        except Exception:
            continue
    if not dropped:
        pytest.skip("engine has no Point fulltext drop procedure")
    try:
        proj._create_fulltext_index("Point", ["content", "search_keys"])
    except Exception as e:
        pytest.skip(f"engine cannot create a fulltext index: {e}")

    # The legacy state, pinned on the CATALOGUE (not only on a query that also
    # returns [] when no index exists at all): the point exists, the index is
    # EXACTLY the pre-#11682 shape, and its quote is not indexed.
    assert _point_fulltext_fields(g) == {"content", "search_keys"}, (
        "fixture not reached: expected the pre-#11682 two-field index, got "
        f"{_point_fulltext_fields(g)}")
    assert [i for i, _ in run_fts_query(
        g, QUOTE_ONLY_TERM, entity_type="point")] == [], (
            "fixture not reached: the two-field index should NOT match a "
            "quote-only term")
    assert proj._schema_is_current() is False, (
        "a two-field Point index must read NOT current — the schema half of "
        "the legacy branch's gate (#5444, one widening later)")

    proj._ensure_indexes()

    fields = _point_fulltext_fields(g)
    assert fields and "quote" in fields, (
        f"the sweep must widen the Point index to carry the quote, got {fields}")
    assert proj._schema_is_current() is True
    assert pid in [i for i, _ in run_fts_query(
        g, QUOTE_ONLY_TERM, entity_type="point")], (
            "the ALREADY-WRITTEN point must become findable by its verbatim "
            "quote — the FULLTEXT recreate re-indexes existing nodes")
