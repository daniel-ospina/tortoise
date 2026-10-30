"""#10121 — a boundary tie must not truncate the NEWEST lexical match.

The graph-side lexical scorer returns ONE score for a whole corpus of look-alike
documents (measured: 2.0 across 131 documents, embedded and docker FalkorDB
alike), so the tie IS the ranking. #3019 resolved it with ``node.id ASC``, and
because ``id`` is minted from a millisecond timestamp
(``ids.ulid()`` -> ``<ts-hex>-<uuid>``) that is OLDEST-first: the record created
last took the last pool slot and, past the pool depth (``DEFAULT_POOL_SIZE =
120``), was truncated out of the leg entirely and carried no score at all. That
is #8488's production reading — ``"fts": null`` for the point that had just been
written.

The fix is the shared ``search_engine.recency_tie_clause``: recency breaks the
tie, ``id DESC`` makes the order total. What is pinned here:

* the acceptance sweep — the fresh match stays INSIDE the leg's pool at every
  corpus size, with the tie premise asserted so the test cannot pass vacuously
  once the scorer differentiates;
* the two regressions the first attempt at this fix introduced: **id-less**
  rows (``:Object``, which the hosted write path mints without an ``id``) must
  NOT be promoted to the head of the window by a bare ``DESC`` on a NULL, and
  **legacy/re-keyed ids** (``p-NNNNNNN``, ``poi-<sha256>``) must not outrank a
  freshly minted Point;
* the recency-vs-creation ordering itself — ``updatedAt`` before ``createdAt``.

Mutation map (each revert must red a named test):
  * ``recency_tie_clause`` -> ``f"{alias}.{id_field} ASC"``  -> the sweep + rank
    tests red (the fresh match leaves the window);
  * ``coalesce(..., '')`` dropped                           -> the id-less
    Object test reds (NULLs at the head);
  * ``updatedAt`` swapped behind ``createdAt``               -> the
    updated-beats-created test reds.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import recency_tie_clause, run_fts_query

pytestmark = pytest.mark.embedded_only

_SDKS: list[TortoiseSDK] = []

#: ⛔ #8933 RE-PIN (the corpus only — every FAIL VALUE below is unchanged).
#: This module used to spell its query `capstone-9664 write probe 2026-10-26`
#: and claimed the `keep_numeric=False` policy reduced it to
#: `capstone|write|probe`, i.e. that "the #8933 control is untouched here".
#: #8933 IS touched now, and its reopen (the owner ruling on #8933, 2026-10-26)
#: means an identifier-shaped query no longer collapses to its bare words:
#: `build_or_query` emits `capstone|write|probe|capstone9664|"capstone 9664"`
#: and the WRITE side stores `capstone9664` on the target's `search_keys`, so the
#: target and the look-alikes stop tying (measured on the shipped tree:
#: `{1.857143, 2.0}`). That differentiates BY DESIGN — it is the ruling's own
#: goal, not a #10121 regression — so the tie this module measures (the SCORE tie
#: #10121 truncates on) has to be spelled on a corpus the identifier tokenizer is
#: silent on: bare numbers only (`variant 9001` is numeric noise on both sides,
#: no `<letter>-<digits>` span anywhere), which ties at a uniform score.
#: The pin that keeps the re-pin from laundering anything: the corpus/query pair
#: is asserted to carry NO derived alias and NO phrase term below.
#:
#: ⚠️ The fresh point must stay the SAME LENGTH as a look-alike: the BM25 arm
#: normalises by document length, so giving the target a shorter body would
#: separate the scores by a mechanism that has nothing to do with #10121. The
#: look-alikes and the target therefore differ in exactly one numeric token —
#: the same relationship the pre-#8933 corpus had, minus the identifier shape.
#: The digits sit inside PARENTHESES, and that is load-bearing: `probe 9664`
#: (a word, a space, digits) is itself an identifier span on the READ side, so
#: the query would mint `probe9664` + `"probe 9664"` and re-break the tie —
#: measured: `capstone write probe 9664` -> `capstone|write|probe|probe9664|
#: "probe 9664"`. A punctuation guard (`(`) keeps the numeric token numeric.
QUERY = "capstone write probe (9664)"

#: Look-alikes: the SAME shape as the target, differing only in the digits the
#: tokenizer drops. Every one of them scores identically to the target.
def _lookalikes(n: int) -> list[str]:
    return [f"capstone write probe ({9000 + i})" for i in range(n)]


def test_the_tie_corpus_is_one_the_identifier_tokenizer_is_SILENT_on():
    """The uniform-score premise is a property of the SHIPPED tokenizer.

    #8933's mechanism is additive and correct: an identifier-shaped corpus
    differentiates (that is the ruling's intent, pinned in
    ``tests/test_identifier_tokens_8933.py``). This module needs the opposite —
    a corpus the tokenizer cannot tell apart — so the choice is pinned HERE, at
    the corpus, instead of being left to fail as a TIE PREMISE assertion inside
    a 200-point sweep. It also measures the tokenizer the re-pin was made
    against, so a change to `identifier_alias`'s shape surfaces here rather
    than as a corpus that silently stopped tying.
    """
    from tortoise.projection.entities import point_identifier_aliases
    from tortoise.sparse import build_or_query, identifier_alias, identifier_phrase_terms

    assert build_or_query(QUERY) == "capstone|write|probe", build_or_query(QUERY)
    # ... and the arm that would have been added for an identifier still works,
    # so "the corpus derives nothing" is a statement about THIS corpus, not
    # about a tokenizer that stopped deriving.
    assert identifier_alias("capstone-9664") == "capstone9664"
    assert identifier_phrase_terms("capstone-9664") == ['"capstone 9664"']
    for text in (QUERY, *_lookalikes(3)):
        assert point_identifier_aliases({"content": text}) == [], (
            f"the tie corpus derives an alias for {text!r} — it is "
            "identifier-shaped again, and #8933 differentiates identifier "
            "corpora BY DESIGN, so the uniform-score premise cannot hold")


@pytest.fixture(autouse=True)
def _embedder_off(force_sparse_tfidf):
    """Pin the embedder OFF for this module — the write path is not the subject.

    Every assertion here is LEXICAL + recency: none depends on a vector, and the
    only vectors that matter are the ones a test SEEDS by hand (``vecf32`` in the
    scan-branch pin). Unpinned, each ``sdk.create_point`` pays one real
    transformer forward whenever the lane has the model — and this file writes
    **558** of them (the acceptance sweep's 10+50+119+130+200 look-alikes, plus
    the BM25 arm's 21 and the small fixtures). Measured on the D14
    (``embedded_only``) leg, run 40559368109 job 122408095621: the acceptance
    sweep hit pytest's ``--timeout=300`` with its stack inside
    ``sdk.create_point`` -> ``embeddings.encode_for_row`` ->
    ``sentence_transformers.encode`` -> BERT forward, which reddened the leg and
    through it the required ``python-ci-gate``.

    That lane installs ``.[test,embeddings]`` so the bge model is PRESENT; a dev
    lane without a model cache has ``EmbeddingModel.get() is None`` and runs the
    same file in ~17 s — which is exactly the environment-dependence this seam
    exists to remove (#2573/#2772: *"CI may run with the bge embedder cache
    present or absent … any test pinning exact retrieval pool sizes must pin the
    embedder state or it is environment-dependent"*). The seam is
    ``tests/conftest.py``'s ``force_sparse_tfidf``; with it requested, the same
    file performs **0** encodes.

    ⚠️ Not a weakening: the tie premise and the pool sizes are unchanged, and
    ``create_point`` still writes the id/timestamps the ranking reads — only the
    optional vector column (``sdk.py``: *"embedding is optional"*) is skipped.
    """
    return force_sparse_tfidf


@pytest.fixture(autouse=True)
def _reclaim_sdks():
    yield
    while _SDKS:
        sdk = _SDKS.pop()
        with contextlib.suppress(Exception):
            sdk.close()
        tree = os.path.dirname(sdk._db_path) if sdk._db_path else None
        if tree:
            shutil.rmtree(tree, ignore_errors=True)


def _sdk() -> TortoiseSDK:
    db_path = os.path.join(tempfile.mkdtemp(prefix="tie10121_"), "test.db")
    sdk = TortoiseSDK(db_path, namespace=f"tie10121_{os.urandom(6).hex()}")
    _SDKS.append(sdk)
    return sdk


# ── the acceptance sweep ──────────────────────────────────────────────────


def test_fresh_match_survives_the_tie_at_every_corpus_size():
    """The point created LAST is inside the leg's pool at every N.

    FAIL VALUE: with the pre-#10121 clause (``id ASC``) the fresh point ranks
    LAST of N+1, so at ``limit=10`` it is absent from the window and — once N
    exceeds the leg's own pool depth — it carries no score at all.
    """
    for n in (10, 50, 119, 130, 200):
        sdk = _sdk()
        graph = sdk._get_proj().g
        for content in _lookalikes(n):
            sdk.create_point("statement", content)
        fresh = sdk.create_point("statement", QUERY)["id"]

        rows = run_fts_query(graph, QUERY, limit=10)
        ids = [pid for pid, _ in rows]
        assert len(rows) == 10, f"FIXTURE NOT REACHED (n={n}): leg not saturated"
        full = run_fts_query(graph, QUERY, limit=n + 5)
        assert len({round(s, 6) for _, s in full}) == 1, (
            f"TIE PREMISE BROKEN (n={n}): the corpus no longer scores "
            f"uniformly — this sweep exists for the all-tied case")
        assert fresh in ids, (
            f"n={n}: a boundary tie must not truncate the NEWEST match out of "
            f"the leg — fresh point absent from {len(rows)} rows (#10121)"
        )


def test_the_tie_ranks_the_fresh_match_first_not_last():
    """Direction, not just presence: newest-first within the tie.

    Also asserts the TIE PREMISE, so this fails loudly (not vacuously) if the
    scorer ever differentiates and the case stops existing.
    """
    sdk = _sdk()
    graph = sdk._get_proj().g
    for content in _lookalikes(20):
        sdk.create_point("statement", content)
    fresh = sdk.create_point("statement", QUERY)["id"]

    rows = run_fts_query(graph, QUERY, limit=25)
    assert rows, "FIXTURE NOT REACHED: the query matched nothing"
    scores = {round(score, 6) for _, score in rows}
    assert len(scores) == 1, (
        "TIE PREMISE BROKEN: the corpus no longer scores uniformly "
        f"({scores}) — this test exists for the all-tied case; if the scorer "
        "now differentiates, the #10121 truncation risk has moved, not gone"
    )
    ranked = [pid for pid, _ in rows]
    assert ranked[0] == fresh, (
        "within an all-tied corpus the newest match must rank first; got "
        f"rank {ranked.index(fresh) + 1} of {len(ranked)} (#10121)"
    )


# ── the two regressions the first attempt introduced ──────────────────────


def test_id_less_rows_do_not_reach_the_head_of_the_window():
    """An id-less row must sort as OLDEST, not first.

    The engine sorts NULL as the LARGEST value, so a bare ``updatedAt DESC``
    tie promotes every undated row to the HEAD of the window and the leg's
    ``LIMIT`` then spends its whole pool on rows the caller discards (the
    hosted write path mints ``:Object`` nodes without an ``id``, so this is a
    live population, not a synthetic one). ``coalesce(..., '')`` puts them at
    the tail.

    FAIL VALUE: drop the ``coalesce`` and the two id-bearing objects are pushed
    out of a 3-row window by the three id-less ones.
    """
    sdk = _sdk()
    graph = sdk._get_proj().g
    # EVERY object carries the SAME name AND content, so every one of them
    # matches the query's token set and they tie on score — the tie is the whole
    # subject of this test. Two earlier drafts failed the premise: using the name
    # as content scored the dated rows higher (4.0 vs 1.0), and then keeping the
    # token in the CONTENT only was no better, because the Object fulltext index
    # is on ``name``
    # (``projection/__init__.py``: ``("Object", "name", "FULLTEXT")``).
    for name in ("obj-dated-a", "obj-dated-b", "obj-dated-c"):
        graph.query(
            "CREATE (o:Object {name:$name, objectKind:'concept', "
            "content:'obj dated probe'})",
            params={"name": name},
        )
    for name in ("obj-dated-d", "obj-dated-e"):
        graph.query(
            "CREATE (o:Object {id:$name, name:$name, objectKind:'concept', "
            "content:'obj dated probe', createdAt:'2026-01-01T00:00:00Z', "
            "updatedAt:'2026-01-01T00:00:00Z'})",
            params={"name": name},
        )

    rows = run_fts_query(graph, "obj dated", entity_type="object", limit=3,
                         excluded_statuses=())
    ids = [pid for pid, _ in rows]
    assert len(ids) == 3, f"FIXTURE NOT REACHED: expected 3 rows, got {rows}"
    # TIE PREMISE: the five Objects must score identically or this test measures
    # the score key, not the NULL guard.
    full = run_fts_query(graph, "obj dated", entity_type="object", limit=10,
                         excluded_statuses=())
    assert len({round(s, 6) for _, s in full}) == 1, (
        "TIE PREMISE BROKEN: the Object fixture no longer ties on score "
        f"({full}) — the recency key is not what is being tested here"
    )
    # The guard's contract: every IDENTIFIABLE row precedes every id-less one.
    # (An id-less row may still fill a leftover slot — the point is that it
    # cannot OCCUPY one ahead of a row the caller can address.)
    first_none = ids.index(None) if None in ids else len(ids)
    assert first_none >= 2, (
        "an id-less Object reached the head of the window — the tie is sorting "
        f"NULL first, so the pool is spent on unidentifiable rows (#10121 was "
        f"fixed with coalesce(..., '') for exactly this): {ids}"
    )
    assert ids[:first_none] == ["obj-dated-e", "obj-dated-d"], (
        f"the dated Objects must fill the window's head, newest id first: {ids}"
    )


def test_legacy_ids_do_not_outrank_a_freshly_minted_point():
    """A legacy id spelling must not beat a new point on a tie.

    Legacy and re-keyed Point ids (``p-NNNNNNN``, ``poi-<sha256>``) sort
    lexicographically ABOVE every ``<ts-hex>-<uuid>`` id, so a tie keyed on the
    id alone ranks every legacy point above every new one — the #10121 defect
    re-appearing inverted on a normalised graph. The recency key must decide,
    with ``coalesce`` keeping the undated legacy rows at the tail.

    FAIL VALUE: order on ``id DESC`` alone and the two legacy rows take the
    head of the window.
    """
    sdk = _sdk()
    graph = sdk._get_proj().g
    for pid in ("p-0000001", "poi-" + "a" * 26):
        graph.query(
            "CREATE (n:Point {id:$id, content:'capstone-9664 write probe', "
            "pointKind:'statement'})",
            params={"id": pid},
        )
    fresh = sdk.create_point("statement", QUERY)["id"]

    rows = run_fts_query(graph, QUERY, limit=10)
    ids = [pid for pid, _ in rows]
    assert fresh in ids, "FIXTURE NOT REACHED: the fresh point is not in the leg"
    assert ids[0] == fresh, (
        "a legacy/re-keyed id outranked a freshly minted Point on a tie — the "
        f"recency key is not deciding (#10121): {ids}"
    )


# ── the recency key's own ordering ────────────────────────────────────────


def test_updated_at_beats_created_at():
    """A tie surfaces the LATEST REVISION, not the latest creation.

    Two points, both matching; the OLDER one carries the NEWER ``updatedAt``.
    ``updatedAt`` first puts it in front — which is the point of the ordering
    (a dedup hit or ``update_point`` bumps ``updatedAt``; a fresh row stamps
    both).

    FAIL VALUE: swap the two properties in ``recency_tie_clause`` and this
    assertion flips.
    """
    sdk = _sdk()
    graph = sdk._get_proj().g
    for pid, created, updated in (
        ("edited-late", "2026-01-01T00:00:00Z", "2026-10-01T00:00:00Z"),
        ("created-late", "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z"),
    ):
        graph.query(
            "CREATE (n:Point {id:$id, content:'capstone-9664 write probe', "
            "pointKind:'statement', createdAt:$c, updatedAt:$u})",
            params={"id": pid, "c": created, "u": updated},
        )

    ids = [pid for pid, _ in run_fts_query(graph, QUERY, limit=10)]
    assert ids, "FIXTURE NOT REACHED: the leg returned no rows"
    assert ids[0] == "edited-late", (
        "the tie must follow the latest REVISION (updatedAt before createdAt), "
        f"not the latest creation: {ids}"
    )


def test_recency_tie_clause_is_one_shared_expression():
    """The clause lives in ONE place, with the NULL guard built in.

    A unit pin on the shared helper: the six legs interpolate this string, so
    the guard and the key order are asserted once here rather than six times.
    """
    clause = recency_tie_clause("node", "id")
    assert clause == (
        "CASE WHEN coalesce(node.updatedAt, '') = '' "
        "THEN coalesce(node.createdAt, '') ELSE node.updatedAt END DESC, "
        "coalesce(node.id, '') DESC"
    ), clause
    # Both guards are load-bearing: a bare property DESC sorts NULL FIRST, and
    # a plain coalesce would let an EMPTY updatedAt suppress a valid createdAt.
    assert "coalesce(node.updatedAt, '') = ''" in clause, clause
    assert "coalesce(node.createdAt, '')" in clause, clause


def test_an_empty_updated_at_falls_through_to_created_at():
    """The empty-string trap the CASE closes (measured on both engines).

    ``coalesce`` stops at the first non-NULL, so ``updatedAt: ''`` would have
    been taken as a value and the row would have sorted as OLDEST despite a
    valid ``createdAt``. No writer stamps ``''`` today; the clause no longer
    depends on that staying true.
    """
    sdk = _sdk()
    graph = sdk._get_proj().g
    graph.query(
        "CREATE (n:Point {id:'empty-updated', content:'capstone-9664 write "
        "probe', pointKind:'statement', createdAt:'2026-09-01T00:00:00Z', "
        "updatedAt:''})")
    graph.query(
        "CREATE (n:Point {id:'older-dated', content:'capstone-9664 write "
        "probe', pointKind:'statement', createdAt:'2026-01-01T00:00:00Z', "
        "updatedAt:'2026-01-01T00:00:00Z'})")
    ids = [pid for pid, _ in run_fts_query(graph, QUERY, limit=10)]
    assert ids[:1] == ["empty-updated"], (
        "a row with an EMPTY updatedAt must fall through to its createdAt "
        f"rather than sorting as oldest: {ids}")


def test_writers_stamp_zero_padded_iso_timestamps():
    """The lexicographic comparison assumes zero-padded ISO-8601.

    Measured: ``'2026-1-5T00:00:00Z'`` sorts ABOVE ``'2026-06-01T00:00:00Z'``,
    so an unpadded stamp would silently invert recency. The assumption is
    pinned at its SOURCE — the writer — rather than left implicit in the clause.
    """
    import re

    sdk = _sdk()
    pid = sdk.create_point("statement", "a point whose stamps are pinned")["id"]
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) RETURN n.createdAt, n.updatedAt",
        params={"i": pid}).result_set
    assert rows, "FIXTURE NOT REACHED: the point was not written"
    from datetime import datetime

    suffixes: set[str] = set()
    stamp = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
    for value in rows[0]:
        if value is None:
            continue
        text = str(value)
        assert stamp.match(text), (
            f"a writer stamped a non-zero-padded timestamp ({value!r}); the "
            "recency tie compares these as STRINGS, so padding is required")
        # The SUFFIX matters as much as the padding: ``Z`` and ``+00:00`` are
        # the same instant but different strings, and the tie is a string
        # comparison — one authoring format, asserted here.
        datetime.fromisoformat(text)
        assert text.endswith(("+00:00", "Z")), (
            f"a writer stamped a non-UTC-offset suffix ({value!r}) — the tie "
            "compares these as STRINGS, so the timezone spelling is part of "
            "the ordering")
        suffixes.add(text[-6:] if text.endswith("+00:00") else text[-1:])
    assert suffixes, (
        "FIXTURE NOT REACHED: no stamp was read, so the suffix check is vacuous")
    assert len(suffixes) == 1, (
        "this writer emits TWO UTC suffix spellings — the tie is a string "
        f"compare, so ``Z`` and ``+00:00`` are not interchangeable: {suffixes}")


# ── the two legs that rank themselves ─────────────────────────────────────


def test_bm25_arm_keeps_the_newest_match_in_its_own_window():
    """The arm ranks its own corpus, so it needs the same rule.

    Measured before this pin existed: the corpus WINDOW was newest-first but
    ``bm25_rank`` still sorted ``(-score, id)`` ASC, so on a 21-row tie the
    just-created point came back at rank 20 and was dropped by ``limit``.

    FAIL VALUE: revert ``bm25_rank``'s key to ``(-score, id)`` and the fresh
    point leaves the arm's window.
    """
    from tortoise.search_engine import run_bm25_query

    sdk = _sdk()
    graph = sdk._get_proj().g
    for content in _lookalikes(20):
        sdk.create_point("statement", content)
    fresh = sdk.create_point("statement", QUERY)["id"]

    rows = run_bm25_query(graph, QUERY, limit=5, excluded_statuses=())
    ids = [pid for pid, _ in rows]
    assert ids, "FIXTURE NOT REACHED: the arm returned no rows"
    assert fresh in ids, (
        "the BM25 arm must keep the NEWEST match inside its own window, not "
        f"rank it last and truncate it (#10121): {ids}"
    )

    # The WIRING assertion: the LOWEST-id look-alike (last under the id key
    # alone) is stamped NEWEST, so it can lead the arm only if
    # ``run_bm25_query`` really passes its recency map into ``bm25_rank``.
    # Without this, an edit dropping ``recency=recency`` at the call site stays
    # invisible — the fresh point also holds a high id, so the assertion above
    # passes on the id key alone.
    others = [pid for pid, _ in run_bm25_query(
        graph, QUERY, limit=25, excluded_statuses=()) if pid != fresh]
    assert others, "FIXTURE NOT REACHED: no look-alike to restamp"
    restamped = min(others)
    graph.query(
        "MATCH (n:Point {id:$i}) SET n.updatedAt = '2027-01-01T00:00:00Z'",
        params={"i": restamped})
    ids2 = [pid for pid, _ in run_bm25_query(
        graph, QUERY, limit=5, excluded_statuses=())]
    assert ids2 and ids2[0] == restamped, (
        "a look-alike stamped NEWER than the fresh point must LEAD the arm "
        "even though its id sorts last — otherwise the recency map is not "
        f"reaching bm25_rank (#10121 review): {ids2}"
    )


def test_vector_scan_branch_breaks_equal_distance_ties_newest_first():
    """The scan branch and the index branch must agree on the tie.

    #10121's review found the two branches tie-breaking in OPPOSITE directions
    (scan newest-first, index oldest-first), which made the fused order depend
    on whether an HNSW index happened to exist. Both now use the shared rule;
    this pins the SCAN side, and the index side is pinned by
    ``tests/test_search_engine_gaps.py::TestRunVectorQuery``.

    FAIL VALUE: revert the scan branch to ``id ASC`` — or drop the recency
    key and leave ``id DESC`` — and the three connected points come back in a
    different order (see the permutation note at the stamps below).
    """
    from tortoise.search_engine import run_vector_query

    sdk = _sdk()
    graph = sdk._get_proj().g
    vec = [0.1] * 4
    # The recency order is a NON-MONOTONIC permutation of the ids on purpose:
    # newest is ``mmm``, then ``zzz``, then ``aaa``. Ordered on the id alone —
    # ASCENDING (the pre-#10121 clause) or DESCENDING (the determinism key with
    # the recency key dropped) — the answer is different, so this ordering is
    # reachable through the recency key ALONE. (A fixture where recency and id
    # agree one way passes under the very mutation it claims to catch.)
    stamps = {
        "vec-tie-mmm": "2026-12-01T00:00:00Z",
        "vec-tie-zzz": "2026-06-01T00:00:00Z",
        "vec-tie-aaa": "2026-01-01T00:00:00Z",
    }
    for pid, stamp in stamps.items():
        graph.query(
            "CREATE (n:Point {id:$id, content:$id, pointKind:'statement', "
            "createdAt:$c, updatedAt:$c})",
            params={"id": pid, "c": stamp})
        graph.query(
            "MATCH (n:Point {id:$id}) SET n.embedding = vecf32($v)",
            params={"id": pid, "v": vec})

    rows = run_vector_query(graph, vec, limit=10, is_embedded=True,
                            excluded_statuses=())
    ids = [pid for pid, _ in rows if str(pid).startswith("vec-tie-")]
    assert len(ids) == 3, f"FIXTURE NOT REACHED: expected 3 rows, got {rows}"
    assert ids == ["vec-tie-mmm", "vec-tie-zzz", "vec-tie-aaa"], (
        "equal-distance rows on the SCAN path must be RECENCY-then-id DESC — "
        "this ordering is neither the id ASC nor the id DESC one, so nothing "
        f"but the recency key can produce it: {ids}"
    )


def test_hop_expansion_breaks_equal_hop_ties_newest_first():
    """The hop-expansion leg's tie DIRECTION is pinned behaviourally.

    #10121's census names six sites. Five carry a behavioural pin; this one
    carried only ``assert "ORDER BY hops" in cypher``
    (``tests/test_search_engine_gaps.py::TestExpandStructuralHops::test_hop1_
    direct_neighbor_full_score``), which passes under ANY tie — including the
    oldest-first one this change removed — and the AST census cannot cover it
    either, because it asks whether the helper is *called* inside the function,
    not which direction or alias it was called with. So a flipped direction or a
    wrong alias on this leg reds nothing (review P2). This closes it.

    FAIL VALUE: order on ``hops ASC, n.id ASC`` again — or drop the recency
    key and leave ``id DESC`` — and the three equal-hop peers come back in a
    different order (see the permutation note at the stamps below).
    """
    from tortoise.search_engine import expand_structural_hops

    sdk = _sdk()
    graph = sdk._get_proj().g
    graph.query("CREATE (n:Point {id:'hop-seed', content:'hop seed', "
                "pointKind:'statement', is_operator:false})")
    # The recency order is a NON-MONOTONIC permutation of the ids on purpose:
    # newest is ``mmm``, then ``zzz``, then ``aaa``. Ordered either ASCENDING or
    # DESCENDING on the id alone the answer is different (``aaa,mmm,zzz`` /
    # ``zzz,mmm,aaa``), so this fixture is reachable ONLY through the recency
    # key — a fixture where recency and id agree one way passes under the
    # mutation it claims to catch, which is how this pin's first draft was
    # vacuous (it passed with the clause reverted to ``n.id ASC``).
    stamps = {
        "hop-peer-mmm": "2026-12-01T00:00:00Z",
        "hop-peer-zzz": "2026-06-01T00:00:00Z",
        "hop-peer-aaa": "2026-01-01T00:00:00Z",
    }
    for pid, stamp in stamps.items():
        graph.query(
            "CREATE (n:Point {id:$id, content:'hop peer', "
            "pointKind:'statement', is_operator:false, createdAt:$c, "
            "updatedAt:$c})",
            params={"id": pid, "c": stamp})
        graph.query(
            "MATCH (s:Point {id:'hop-seed'}), (n:Point {id:$id}) "
            "CREATE (s)-[:IMPL]->(n)", params={"id": pid})

    rows = expand_structural_hops(graph, ["hop-seed"], excluded_statuses=())
    assert len(rows) == 3, f"FIXTURE NOT REACHED: expected 3 peers, got {rows}"
    # HOP PREMISE: all three at hop 1, or ``hops ASC`` decides and the recency
    # key never engages.
    assert {score for _, score in rows} == {1.0}, (
        f"HOP PREMISE BROKEN: the three peers are not all one hop out: {rows}")
    got = [pid for pid, _ in rows]
    assert got == ["hop-peer-mmm", "hop-peer-zzz", "hop-peer-aaa"], (
        "equal-hop peers must come back RECENCY-then-id DESC — this ordering "
        "is neither the id ASC nor the id DESC one, so nothing but the recency "
        f"key can produce it: {got}")


def test_the_recency_key_normalises_an_absent_identity():
    """The Python-side key must be TOTAL — a None pid is a live population.

    ``:Object`` rows are written id-less, so ``recency_tie_key`` receives
    ``None``. A raw ``None`` in a sort key raises ``TypeError`` and the vector
    index branch swallows it, demoting the read to a full scan (round-3
    review's P1). Pinned directly, engine-free.
    """
    from tortoise.search_engine import recency_tie_key

    assert recency_tie_key(None, None) == ("", "")
    assert recency_tie_key("2026-01-01T00:00:00Z", "abc") == (
        "2026-01-01T00:00:00Z", "abc")
    # A numeric stamp keeps its own ordering direction — stated, not fixed.
    assert recency_tie_key(20261201, "abc") == ("20261201", "abc")


def test_an_id_less_row_does_not_lead_a_tie_group_without_timestamps():
    """Both halves of the rule must send an absent identity to the TAIL.

    #10121 round-4 review: the cypher id key was a BARE ``<id> DESC``, and the
    engine sorts NULL as the LARGEST value — so inside a group whose RECENCY
    ties (every row undated), the id key decided and put the ID-LESS rows at
    the HEAD, where the caller discards them (``assembly.py``: ``if oid``).
    The Python half (:func:`recency_tie_key`) normalised ``None`` to ``''``;
    the cypher half did not, and the two disagreed on exactly the population
    the object leg serves. Measured on both engines: bare ``id DESC`` →
    ``[None, None, 'obj-aaa']``; with ``coalesce(id, '')`` → ``['obj-aaa',
    None, None]``.

    FAIL VALUE: drop the ``coalesce`` from the id key and the id-less rows take
    the first slots of this window.
    """
    sdk = _sdk()
    graph = sdk._get_proj().g
    # Every row is UNDATED *and carries the same NAME TOKENS* — the Object
    # fulltext index is on ``name``, so a name that matched fewer query terms
    # would let the SCORE decide and the id key would never engage (the trap an
    # earlier draft of this test fell into). With all four names holding
    # ``obj dated``, the scores tie, recency ties at ``''``, and the id key
    # alone decides.
    graph.query("CREATE (o:Object {id:'obj-aaa', name:'obj dated a', "
                "objectKind:'concept', content:'obj dated probe'})")
    for suffix in ("b", "c", "d"):
        graph.query(
            "CREATE (o:Object {name:$n, objectKind:'concept', "
            "content:'obj dated probe'})", params={"n": f"obj dated {suffix}"})

    full = run_fts_query(graph, "obj dated", entity_type="object", limit=10,
                         excluded_statuses=())
    assert len({round(s, 6) for _, s in full}) == 1, (
        "TIE PREMISE BROKEN: the Object rows must score identically, or the id "
        f"key is not what this test measures: {full}"
    )
    rows = run_fts_query(graph, "obj dated", entity_type="object", limit=2,
                         excluded_statuses=())
    ids = [pid for pid, _ in rows]
    assert len(ids) == 2, f"FIXTURE NOT REACHED: expected 2 rows, got {rows}"
    assert ids[0] == "obj-aaa", (
        "an id-less row took the head of a tie group whose recency is equal — "
        "the cypher id key is sorting NULL first, so the pool is spent on rows "
        f"the caller cannot address (#10121 round-4 P1): {ids}"
    )
