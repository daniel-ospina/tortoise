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
  * revert the BM25 corpus read for the POINT family to content ∪ search_keys
        -> ``test_the_bm25_mirror_finds_the_quote_too`` reds;
  * join ``quote`` for EVERY family the BM25 leg serves (the pre-review
    shape, one join instead of the per-family one)
        -> ``test_the_bm25_mirror_is_per_family`` reds: a ``:Source`` whose
           only record of the term is its ``quote`` becomes a BM25 hit while
           its own FTS index (``_searchText``) cannot see it (#11706 P2-5);
  * drop the ``quote`` from ``_SNAPSHOT_QUERY``
        -> the ``"quote" in _SNAPSHOT_QUERY`` assertion reds, and so does
           ``test_every_declared_field_is_selected_by_the_snapshot_mirror``,
           which walks the WHOLE declared set against that query;
  * drop ``"quote": r[7]`` from the snapshot's ROW MAPPING (or shift it)
        -> the ``row["quote"] == VERBATIM`` / ``row["has_answer"] is False``
           assertions in `test_the_bm25_mirror_finds_the_quote_too` red;
  * revert the snapshot's JOIN (``texts = [point_index_text(p) …]``) or its
    scorer join to ``index_text(content, search_keys)``
        -> the masked-embedder arm in that test reds (the sparse vocabulary
           loses ``quokka`` and ``search_snapshot`` misses the point), the
           legacy no-vector arm reds too, and
           `test_the_bm25_corpus_select_and_join_carry_every_declared_field`
           goes on red for the BM25 mirror's own select/join;
  * revert ``search_engine.fallback_tfidf``'s join to
    ``index_text(content, search_keys)``
        -> ``test_the_fallback_tfidf_join_reads_the_quote`` reds (the
           last-resort TF-IDF entry point, whose inputs carry ``quote`` from
           the eval ingest and ``assembly``);

⚠️ FIXTURE PROVENANCE: ``VERBATIM`` is CONSTRUCTED prose (authored for this
module, punctuated the way a real utterance is), NOT a captured transcript —
the repo ships no real quote corpus and its test extractors are fakes. What is
*measured* rather than inferred is the recall property itself:
``REAL_SENTENCE_QUERY`` is a differently-worded question whose only overlap
with the stored fact lives in the ``quote``, and it must find the point.

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

from tests._live_utils import _skip_unless_live_uri, mask_embedder
from tests._index_settle import index_rows_after_settle
from tortoise.search_engine import (
    reset_circuit_breakers,
    run_bm25_query,
    run_fts_query,
)
from tortoise.sdk import TortoiseSDK

# CONSTRUCTED prose (see the module docstring's provenance note): punctuated
# and quoted exactly as a transcript records an utterance, the lossy paraphrase
# the fact is stored as, and a decoy so a "returns something" leg cannot pass
# for a hit.
VERBATIM = ('Ana, on the Q3 close: "we adopted the QUOKKA migration plan for '
            'the ledger — and it\'s already paid off!"')
PARAPHRASE = "The team chose the ledger migration plan"
DECOY = "an unrelated note about coffee machines"
QUOTE_ONLY_TERM = "QUOKKA"          # only in the quote
CONTENT_TERM = "ledger"             # in the paraphrase
ABSENT_TERM = "zzquartzitezz"       # in neither field
# A real-shaped question: every token it shares with the stored fact (Ana, Q3,
# close, adopted) lives in the QUOTE — the paraphrase contributes none — so a
# hit is attributable to the quote and not to the paraphrase.
REAL_SENTENCE_QUERY = "what did Ana say we adopted at the Q3 close?"


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


def _point_fulltext_fields_from_rows(rows) -> set[str] | None:
    """The Point FULLTEXT field set, read from the row that carries FULLTEXT.

    Row-per-label is what this engine returns today, but taking the LAST Point
    row would read another index's fields if the engine ever split them; the
    FULLTEXT row is the same rule ``_schema_is_current`` and the migration
    probe use. Rows-based so a caller that WAITED for the catalogue can reuse
    the read it settled on instead of sampling a second time.
    """
    for row in rows:
        if not row or row[0] != "Point":
            continue
        kinds = row[2] or {}
        fields = {f for f, ks in kinds.items()
                  if "FULLTEXT" in {str(k).upper() for k in (ks or ())}}
        if fields:
            return fields
    return None


def _point_fulltext_fields(graph) -> set[str] | None:
    return _point_fulltext_fields_from_rows(
        graph.query("CALL db.indexes()").result_set)


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

    # MEASURED recall claim (not inferred from the tokeniser): a user asking in
    # their OWN words finds the punctuated, quoted utterance.
    assert pid in [i for i, _ in run_fts_query(
        graph, REAL_SENTENCE_QUERY, entity_type="point")], (
            "a real, differently-worded question must find the punctuated "
            "verbatim quote (#11682's whole point)")


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
    #
    # The embedder is masked so `build_snapshot` takes its TF-IDF path: that is
    # what makes the JOIN observable at all — the sparse vocabulary is built
    # from the corpus text, so "quokka" can only be in it if the join read the
    # quote. With a dense model the joined text would be hidden inside vectors
    # and the assertions below could pass either way (#11706 round 2, F1).
    with mask_embedder():
        snap = fs.build_snapshot(sdk._get_proj())
        assert snap is not None, "the snapshot did not build on a two-point graph"
        assert snap["vectorizer"] is not None, (
            "the snapshot took its DENSE path despite the mask — the join is "
            "not observable here")
        vocab = {str(t).casefold() for t in snap["vectorizer"].vocabulary_}
        assert QUOTE_ONLY_TERM.casefold() in vocab, (
            "the snapshot's CORPUS TEXT does not contain the quote's token — "
            "the build join was reverted to content ∪ search_keys")
        snap_hits = [r["id"] for r in fs.search_snapshot(
            QUOTE_ONLY_TERM, snap, threshold=0.01)]
        assert pid in snap_hits, (
            "the degraded lane cannot answer a verbatim quote; "
            f"hits={snap_hits}")
        assert snap_hits.index(pid) == 0 or len(snap_hits) == 1, (
            f"the quote-bearing point did not win its own query: {snap_hits}")

    row = next(p for p in snap["points"] if p["id"] == pid)
    assert row["quote"] == VERBATIM, (
        "the snapshot's row mapping did not carry the verbatim quote; "
        f"row={row}")
    assert row["has_answer"] is False, (
        "the row mapping crossed its columns — `has_answer` reads the quote "
        f"column; row={row}")

    # The NO-VECTOR legacy scorer is the OTHER reader of the same corpus text:
    # when the cached vectors are missing/failed, ``search_snapshot`` rebuilds
    # the text per point at serve time. Dropping the vectors must not lose the
    # quote, or the tier that answers on a graph with no vectorizer at all is
    # the one mirror nobody pinned (#11706 review P1-2).
    # ⛔ The mask is load-bearing HERE TOO: ``search_points`` re-resolves an
    # embedder unless ``force_sparse`` is set (``embeddings.py``:
    # ``model = None if force_sparse else EmbeddingModel.get()``), so outside
    # the mask this "no-vector" arm would score DENSELY on any lane where a
    # model resolves and would return the point for ANY query — blind to the
    # join it claims to pin (#11706 review, round 4).
    with mask_embedder():
        legacy_only = dict(snap, doc_vecs=None, vectorizer=None, model_id=None)
        assert pid in [r["id"] for r in fs.search_snapshot(
            QUOTE_ONLY_TERM, legacy_only, threshold=0.01)], (
                "the snapshot's legacy in-memory scorer must index the quote "
                "too")


def test_the_bm25_mirror_is_per_family(sdk) -> None:
    """``quote`` is a POINT field — the leg must not index it for ``:Source``.

    ``run_bm25_query`` serves the document/source family from the same Cypher,
    but that family's own FTS index is ``Source._searchText`` alone. Joining a
    Point-only field into its corpus text would be the very asymmetry this
    change exists to kill, one field over (#11706 review P2-5): the BM25 leg
    would match text no other lexical surface on that label can see.

    FAIL VALUE: join ``POINT_INDEX_TEXT_FIELDS`` for every family (the shape
    this branch had before the review) — a ``:Source`` carrying a ``quote``
    becomes a BM25 hit with no FTS hit behind it.
    """
    g = sdk._get_proj().g
    g.query(
        "CREATE (s:Source {url: $url, _searchText: $text, quote: $quote})",
        params={"url": f"https://example.test/{uuid.uuid4().hex[:8]}",
                "text": DECOY, "quote": VERBATIM},
    )

    # The family IS served, and the leg sees its _searchText (the control).
    assert [i for i, _ in run_bm25_query(
        g, "coffee", entity_type="source", limit=10, excluded_statuses=())], (
            "the :Source family must stay served — the coalesce arm supplies "
            "its _searchText")
    # …but a term only in the Source's ``quote`` is NOT this family's index
    # text: the leg mirrors the label's own FTS field set.
    assert [i for i, _ in run_bm25_query(
        g, QUOTE_ONLY_TERM, entity_type="source", limit=10,
        excluded_statuses=())] == [], (
            "a :Source's ``quote`` is not one of its FTS fields — the BM25 "
            "corpus text must not index text no lexical surface on that "
            "label holds (#11706 review P2-5)")


def test_the_bm25_corpus_select_and_join_carry_every_declared_field() -> None:
    """Hermetic: the BM25 arm's COLUMN LIST and its join, both from the constant.

    The declaration cannot widen a mirror's select (``_SNAPSHOT_QUERY`` had to
    be edited by hand for ``quote``), so this walks the declared set against the
    Cypher the arm actually emits, then proves the JOIN reads the quote: a
    quote-only query must return the row whose only record of it is the quote.
    ``bm25_scores`` OMITS documents that share no query token, so a reverted
    join makes the hit ABSENT rather than zero-scored — the absence here is a
    real signal, not an empty corpus (#11706 round 2, F1/F2).
    """
    from types import SimpleNamespace

    from tortoise.sparse import POINT_INDEX_TEXT_FIELDS

    class _FakeGraph:
        def __init__(self, rows):
            self.rows = rows
            self.cypher = ""

        def query(self, cypher, params=None, timeout=None):
            self.cypher = cypher
            return SimpleNamespace(result_set=self.rows)

    reset_circuit_breakers()
    graph = _FakeGraph([
        ("p1", PARAPHRASE, None, VERBATIM, None),
        ("decoy", DECOY, None, "", None),
    ])
    try:
        hits = dict(run_bm25_query(
            graph, QUOTE_ONLY_TERM, entity_type="point", limit=10,
            excluded_statuses=()))
    finally:
        reset_circuit_breakers()

    for field in POINT_INDEX_TEXT_FIELDS:
        assert (f"n.{field}" in graph.cypher
                or f"coalesce(n.{field}" in graph.cypher), (
                    f"the BM25 corpus read does not select {field!r} — the "
                    "declared Point lexical field set and this mirror drifted")
    assert "p1" in hits, (
        "the BM25 JOIN must read the quote: a quote-only query missed the row "
        f"whose quote holds it; hits={hits}")
    assert "decoy" not in hits


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
    # ⛔ NOT wrapped in a skip. This test IS the migration pin; if the engine
    # cannot build the two-field legacy index, the pin never ran and a green
    # lane would be a lie (#11706 round 2, F3 — the first version swallowed
    # this, including the documented drop/create ordering hazard). The ONLY
    # legitimate skip here is the missing drop procedure above, which is a
    # property of the engine rather than of this change.
    proj._create_fulltext_index("Point", ["content", "search_keys"])

    # The create/drop are NOT synchronous with their return (tests/_index_settle.py
    # documents the hazard: DROP INDEX returns before the indexer applies it),
    # so the pre-state is WAITED FOR, not sampled — the same
    # `index_rows_after_settle` the write-authority suite uses.
    rows = index_rows_after_settle(
        proj,
        until=lambda r: _point_fulltext_fields_from_rows(r)
        == {"content", "search_keys"})

    # The legacy state, pinned on the CATALOGUE (not only on a query that also
    # returns [] when no index exists at all): the point exists, the index is
    # EXACTLY the pre-#11682 shape, and its quote is not indexed.
    assert _point_fulltext_fields_from_rows(rows) == {"content", "search_keys"}, (
        "fixture not reached: expected the pre-#11682 two-field index, got "
        f"{_point_fulltext_fields_from_rows(rows)}")
    assert [i for i, _ in run_fts_query(
        g, QUOTE_ONLY_TERM, entity_type="point")] == [], (
            "fixture not reached: the two-field index should NOT match a "
            "quote-only term")
    assert proj._schema_is_current() is False, (
        "a two-field Point index must read NOT current — the schema half of "
        "the legacy branch's gate (#5444, one widening later)")

    proj._ensure_indexes()

    rows = index_rows_after_settle(
        proj,
        until=lambda r: "quote" in (_point_fulltext_fields_from_rows(r) or set()))
    fields = _point_fulltext_fields_from_rows(rows)
    assert fields and "quote" in fields, (
        f"the sweep must widen the Point index to carry the quote, got {fields}")
    assert proj._schema_is_current() is True
    assert pid in [i for i, _ in run_fts_query(
        g, QUOTE_ONLY_TERM, entity_type="point")], (
            "the ALREADY-WRITTEN point must become findable by its verbatim "
            "quote — the FULLTEXT recreate re-indexes existing nodes")


def test_the_fallback_tfidf_join_reads_the_quote() -> None:
    """``search_engine.fallback_tfidf`` joins the SAME declared set.

    Its inputs are POINT DICTS — the eval ingest and ``assembly`` carry the
    quote — so a join that ignored it would put the two lexical stacks back in
    disagreement on the last-resort tier, the one that runs when every FalkorDB
    strategy has failed. Pinned hermetically (no DB) because this arm is a pure
    function over the dicts it is handed.

    FAIL VALUE: revert its join to ``index_text(content, search_keys)`` and the
    quote-only query ties at zero, so the DECOY — first in the list on purpose —
    takes the single slot (#11706 round 3).
    """
    from tortoise.search_engine import fallback_tfidf

    points = [
        {"id": "decoy", "content": DECOY, "pointKind": "statement"},
        {"id": "p1", "content": PARAPHRASE, "quote": VERBATIM,
         "pointKind": "statement"},
    ]
    hits = [r["id"] for r in fallback_tfidf(
        QUOTE_ONLY_TERM, points, limit=1, force_sparse=True)]
    assert hits == ["p1"], (
        f"fallback_tfidf did not read the quote; hits={hits}")
