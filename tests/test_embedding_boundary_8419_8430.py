"""#8430 / #8419 — the embedding boundary: what we COMPOSE, and what the read can READ.

Two roots, one boundary, one file (the PR that lands them is one change to the
same seam pair):

**#8430 — an UNREADABLE stored embedding must cost its own row, not the leg.**
#8316 made a right-width plain ``List`` READABLE (``vector_write.
stored_embedding_vector_expr``). Two siblings survived it, both measured while
fixing #8316 and both hitting the SAME exit: ``vec.euclideanDistance`` raises
for the whole ``MATCH``, ``run_vector_query`` records ``query_failed`` and
returns ``[]``, so one bad node costs every OTHER node its dense leg.

* a ``List`` of NON-NUMERIC values — the shape filter admits it and ``vecf32``
  then raises ``vectorf32 expects an array of numbers``;
* a ``List`` whose WIDTH cannot pair with the query vector — it casts fine and
  the DISTANCE raises the dimension mismatch.

The read seam now drops such a row (it yields NULL, and the read's own
``WHERE distance IS NOT NULL`` excludes it), so the leg answers from the rows it
CAN read. ⚠️ The residual this file pins rather than fixes: a wrong-width
``Vectorf32`` row still aborts the ``MATCH``, because ``size()`` raises on a
``Vectorf32`` on BOTH engines (measured on embedded FalkorDBLite and
falkordb-server v4.20.6) and a ``Vectorf32`` cannot be iterated to measure it
either — see the seam's docstring.

**#8419 — a document's vector derives from text the node does not HOLD.**
``_upsert_document`` embedded ``title + content`` while ``content`` is retired by
D10 (ONTOLOGY §4.4): the vector was composed from caller-supplied text that was
then discarded, disagreed with the ``_searchText`` its own FTS leg searches, and
was silently REPLACED by a ``--force-re-embed`` run. The fold now embeds the SAME
surface it stores.

Hermetic: the embedder is stubbed, the store is a per-test embedded file. No
provider keys, no network.

Run (embedded lane)::

    env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \\
        uv run pytest tests/test_embedding_boundary_8419_8430.py -q
"""
from __future__ import annotations

import math
from unittest import mock

import pytest

from tortoise.embeddings import EMBEDDING_DIM
from tortoise.sdk import TortoiseSDK

# The store is a real embedded file, so the module is `embedded_only` and its
# stem MUST be registered in all THREE pinned homes: config/ci-surfaces.yml
# `carve_out:`, tests/_embedded.py TEST_NO_REDIRECT_STEMS, and the `expected`
# frozenset in tests/test_markers.py::test_no_redirect_stems_registry_exact.
# Without it a URI-set lane collects the file and SKIPS every case via the
# marker hook — a permanently green, permanently unexecuted gate (the #4047
# shape); with the stem missing from the mirror, test_markers.py reds instead.
pytestmark = pytest.mark.embedded_only

_EMBED_PATCH = "tortoise.embeddings.compute_embedding"
DIM = EMBEDDING_DIM if isinstance(EMBEDDING_DIM, int) else 384


def _vector(seed: float) -> list[float]:
    """A smooth, embedder-shaped vector (no axis a synthetic probe could hit)."""
    return [math.sin(i * 0.017) * 0.4 + seed for i in range(DIM)]


class _StubEmbedder:
    """A TOTAL (never-raising) embedder double — see #8316's file for why."""

    def __call__(self, text: str, *_a, **_k) -> list[float]:
        return _vector(len(text) % 7 * 0.01)


def _dense(sdk: TortoiseSDK, query: list[float]) -> tuple[dict[str, float], str | None]:
    """The shared dense read (scan lane) and its leg-trace reason."""
    from tortoise.search_engine import reset_circuit_breakers, run_vector_query
    reset_circuit_breakers()
    trace: list[dict] = []
    hits = dict(run_vector_query(
        sdk._get_proj().g, query, is_embedded=True, leg_trace=trace))
    return hits, (trace[-1].get("reason") if trace else None)


def _overwrite_embedding(sdk: TortoiseSDK, pid: str, value) -> None:
    sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) SET p.embedding=$v", params={"id": pid, "v": value})


def _typeof(sdk: TortoiseSDK, pid: str) -> str | None:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN typeof(n.embedding)",
        params={"id": pid}).result_set
    return rows[0][0] if rows else None


# ── #8430: a wrong WIDTH costs its own row, not the MATCH ────────────────────

def test_a_wrong_width_list_no_longer_costs_the_healthy_neighbour(tmp_path):
    """RED before: the whole ``MATCH`` raises and NO Point is returned.

    This is ``test_search_engine.py::test_vector_trace_wrong_dim_node_is_query_failed``'s
    root, asserted from the other side: the healthy neighbour must SURVIVE the
    malformed one.
    """
    with mock.patch(_EMBED_PATCH, _StubEmbedder()):
        sdk = TortoiseSDK(str(tmp_path / "width.db"))
        try:
            healthy = sdk.create_point("statement", "healthy neighbour")["id"]
            narrow = sdk.create_point("statement", "narrow")["id"]
            assert _typeof(sdk, narrow) == "Vectorf32", "premise: a typed row"
            _overwrite_embedding(sdk, narrow, [0.1, 0.2])  # the poison
            assert _typeof(sdk, narrow) == "List", "premise: verdict verbatim List"

            hits, reason = _dense(sdk, _vector(0.0))
            assert reason == "ok", (
                f"the dense leg recorded {reason!r} — a 2-wide stored List still "
                "aborts the whole MATCH, so every other Point loses its dense "
                "leg (#8430). If the recording is `ok`, the residue is the "
                "TYPED wrong-width row this file already pins.")
            assert healthy in hits, (
                "the healthy Point vanished from the dense leg because a "
                "neighbour holds a wrong-WIDTH List embedding (#8430)")
            assert narrow not in hits, (
                "the wrong-width row is in the result set — the seam filtered "
                "the MATCH's abort but still let the unreadable row through")
        finally:
            sdk.close()


def test_a_non_numeric_list_no_longer_costs_the_healthy_neighbour(tmp_path):
    """RED before: ``vecf32(['x'] * DIM)`` raises and takes the ``MATCH`` down.

    The width is RIGHT here, so this is the cast's own failure — the residual
    #8430's own comment measured and asked to be closed in the same lazy filter.
    """
    with mock.patch(_EMBED_PATCH, _StubEmbedder()):
        sdk = TortoiseSDK(str(tmp_path / "nonnumeric.db"))
        try:
            healthy = sdk.create_point("statement", "healthy neighbour")["id"]
            junk = sdk.create_point("statement", "junk")["id"]
            _overwrite_embedding(sdk, junk, ["x"] * DIM)
            assert _typeof(sdk, junk) == "List", "premise: a plain List"

            hits, reason = _dense(sdk, _vector(0.0))
            assert reason == "ok", (
                f"the dense leg recorded {reason!r} — a NON-NUMERIC stored List "
                "still aborts the whole MATCH (#8430)")
            assert healthy in hits, (
                "the healthy Point vanished because a neighbour holds a "
                "non-numeric embedding (#8430)")
            assert junk not in hits, (
                "the non-numeric row reached the result set — it cannot be "
                "scored, so it must be dropped, not returned")
        finally:
            sdk.close()


def test_a_boolean_element_list_no_longer_costs_the_healthy_neighbour(tmp_path):
    """#8430 review F1: the element check must be TOTAL, not a ``toFloat`` negative.

    ``toFloat(true)`` RAISES (``Type mismatch: expected String, Integer, Float,
    or Null but was Boolean``, measured on both engines) while
    ``toFloat("x")`` merely returns NULL — so a ``[True] * DIM`` stored value,
    which IS storable (``typeof`` is ``List`` and the width matches), passed the
    string-shaped pin above and still took the WHOLE ``MATCH`` down. A RED
    against a ``toFloat(_e) IS NULL`` guard; GREEN with the positive
    ``typeof(_e) IN ['Integer','Float']`` test.
    """
    with mock.patch(_EMBED_PATCH, _StubEmbedder()):
        sdk = TortoiseSDK(str(tmp_path / "booleans.db"))
        try:
            healthy = sdk.create_point("statement", "healthy neighbour")["id"]
            boolvec = sdk.create_point("statement", "bool vector")["id"]
            _overwrite_embedding(sdk, boolvec, [True] * DIM)
            assert _typeof(sdk, boolvec) == "List", "premise: a plain List"

            hits, reason = _dense(sdk, _vector(0.0))
            assert reason == "ok", (
                f"the dense leg recorded {reason!r} — a BOOLEAN-element stored "
                "List still aborts the whole MATCH, so the element check is not "
                "total (#8430 review F1)")
            assert healthy in hits, (
                "the healthy Point vanished because a neighbour holds a "
                "boolean-element embedding (#8430)")
            assert boolvec not in hits, (
                "a boolean-element embedding reached the result set — vecf32 "
                "raises on it, so it must be dropped")
        finally:
            sdk.close()


def test_a_readable_verbatim_list_is_still_read_and_value_preserving(tmp_path):
    """The narrowing must not cost a RIGHT-width numeric List its read.

    A guard against over-filtering: the seam now inspects every element, and a
    filter that also dropped the caller's legitimate verbatim row would trade
    #8430 for #8316 all over again.
    """
    caller = [0.1234567890123 + 0.01 * i for i in range(DIM)]
    with mock.patch(_EMBED_PATCH, _StubEmbedder()):
        sdk = TortoiseSDK(str(tmp_path / "readable.db"))
        try:
            pid = sdk.create_point("statement", "caller vector")["id"]
            _overwrite_embedding(sdk, pid, caller)
            assert _typeof(sdk, pid) == "List", "premise: verbatim"

            hits, reason = _dense(sdk, caller)
            assert reason == "ok", reason
            assert pid in hits, (
                "a right-width, numeric verbatim List became unreadable — the "
                "#8430 filter is over-broad")
            assert hits[pid] == pytest.approx(1.0, abs=1e-3), (
                f"a self-query did not score ~1.0 ({hits[pid]!r}) — the seam "
                "returned something other than the stored value")
        finally:
            sdk.close()


def test_a_wrong_width_TYPED_vector_still_aborts_the_match(tmp_path):
    """#8430's DECLARED RESIDUAL, pinned so the claim stays verified.

    ``size()`` raises on a ``Vectorf32`` on both engines and a ``Vectorf32``
    cannot be iterated to measure it, so the typed arm is returned UNCHECKED and
    a wrong-width typed row still takes the ``MATCH`` down. This test asserts
    the residual — it is NOT a pass for the defect: when the residual is closed
    (a raw projection/legacy write, or an encoder change with no re-embed), this
    case goes red and must be UPDATED, and the seam docstring with it.
    """
    with mock.patch(_EMBED_PATCH, _StubEmbedder()):
        sdk = TortoiseSDK(str(tmp_path / "typednarrow.db"))
        try:
            sdk.create_point("statement", "healthy neighbour")
            sdk._get_proj().g.query(
                "CREATE (p:Point {id:'narrowtyped', content:'narrow typed', "
                "pointKind:'statement', embedding: vecf32([0.1, 0.2])})")
            assert _typeof(sdk, "narrowtyped") == "Vectorf32", "premise"

            hits, reason = _dense(sdk, _vector(0.0))
            assert reason == "query_failed", (
                f"expected the TYPED wrong-width row to still abort the MATCH "
                f"(the declared #8430 residual) — got {reason!r}. If the "
                "residual was closed, UPDATE this pin and the seam docstring "
                "rather than deleting them.")
            assert hits == {}
        finally:
            sdk.close()


def test_a_store_with_no_row_readable_at_this_width_is_not_a_healthy_empty(
        tmp_path):
    """#8430 review F2 — a DROPPED-everything read is not an empty verdict.

    The width filter is per-row, but nothing used to handle the degenerate case:
    when EVERY embedded row is a value the seam cannot read at the query's own
    width, the result set is empty and the zero-row guard counted
    ``n.embedding IS NOT NULL`` — non-zero — so it recorded ``empty_results``
    with ``degraded=False``. That is a FALSE PASS: ``_vector_leg_healthy`` counts
    it as a healthy arm and ``require_hybrid_read`` certifies a hybrid read over
    a dense leg that answered nothing, which is the "silent `ok`" this same
    change's #8429 half closes one branch over
    (docs/architecture/STORAGE-ARCHITECTURE.md §14.3 V4). Before the seam the
    identical store raised, and the leg recorded ``query_failed`` +
    ``degraded=True`` — the one diagnosis an operator gets that the encoder and
    the store disagree (#1349's class).

    RED before the fix (``empty_results``/healthy); GREEN now
    (``unreadable_embeddings``/degraded).
    """
    from tortoise.exceptions import HybridReadUnavailableError
    from tortoise.search_engine import UNREADABLE_EMBEDDINGS, require_hybrid_read

    with mock.patch(_EMBED_PATCH, _StubEmbedder()):
        sdk = TortoiseSDK(str(tmp_path / "allwidth.db"))
        try:
            only = sdk.create_point("statement", "the only candidate")["id"]
            # A storable, verbatim (PR #3018) row whose width cannot pair with
            # the query vector below — the whole cohort is unreadable here.
            _overwrite_embedding(sdk, only, [0.1, 0.2])
            assert _typeof(sdk, only) == "List", "premise: a plain List"

            trace: list[dict] = []
            from tortoise.search_engine import reset_circuit_breakers, run_vector_query
            reset_circuit_breakers()
            hits = dict(run_vector_query(
                sdk._get_proj().g, _vector(0.0), is_embedded=True,
                leg_trace=trace))
            entry = next(e for e in trace if e["leg"] == "vector")

            assert hits == {}, "premise: the only candidate is dropped"
            assert entry["reason"] == UNREADABLE_EMBEDDINGS, (
                f"the leg recorded {entry['reason']!r} over a store whose only "
                "row the width test dropped — `empty_results` there claims "
                "'no near neighbour' over a candidate set the seam emptied "
                "(#8430 review F2)")
            assert entry["degraded"] is True, (
                "a leg that dropped every candidate must not read as healthy")
            with pytest.raises(HybridReadUnavailableError):
                require_hybrid_read(trace)
        finally:
            sdk.close()


def test_a_readable_row_keeps_an_empty_result_a_healthy_verdict(tmp_path):
    """The other side of the F2 pin: the new reason must not swallow the real one.

    A store whose rows ARE readable at this width has a genuine "no near
    neighbour" answer, and this guard makes sure the readability probe cannot
    turn that into a degradation (the probe measures the WHOLE label, while the
    read's own scope may exclude every row — the excluded row is still proof
    that the store is readable, so ``empty_results`` is the honest verdict).
    """
    from tortoise.search_engine import reset_circuit_breakers, run_vector_query

    with mock.patch(_EMBED_PATCH, _StubEmbedder()):
        sdk = TortoiseSDK(str(tmp_path / "scoped.db"))
        try:
            # #11824 S0 stores the declared synonym `retracted` as `deprecated`,
            # so the EXCLUDED set names the word the row actually holds.
            sdk.create_point("statement", "scoped out", status="retracted")
            trace: list[dict] = []
            reset_circuit_breakers()
            hits = run_vector_query(
                sdk._get_proj().g, _vector(0.0), is_embedded=True,
                excluded_statuses=("deprecated",), leg_trace=trace)
            entry = next(e for e in trace if e["leg"] == "vector")

            assert hits == [], "premise: the only row is excluded by the scope"
            assert entry["reason"] == "empty_results", (
                f"a store with a READABLE embedding must keep `empty_results` "
                f"when the zero rows are the scope's doing — got "
                f"{entry['reason']!r}")
            assert entry["degraded"] is False
        finally:
            sdk.close()


# ── #8419: the document vector derives from the surface the node HOLDS ──────

def _doc_node(sdk: TortoiseSDK, did: str) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$id}) "
        "RETURN s._searchText, s.content, s.embedding IS NOT NULL",
        params={"id": did}).result_set
    assert rows, f"the document :Source {did!r} was not written"
    return {"searchText": rows[0][0], "content": rows[0][1],
            "has_embedding": bool(rows[0][2])}


def test_the_document_embedding_is_composed_from_the_text_the_node_stores(tmp_path):
    """RED before: the encoder receives ``'My doc long body text'``.

    The stored ``_searchText`` is ``'My doc a summary graph'`` — so the vector
    encoded text the node does not hold, which nothing in the graph can
    reproduce (#8419 indicator 1).
    """
    seen: list[str] = []
    calls: list[dict] = []

    def _encoder(text, _dim=None, **kwargs):
        seen.append(text)
        calls.append(kwargs)
        return _vector(0.0)

    with mock.patch("tortoise.embeddings.encode_for_row", _encoder):
        sdk = TortoiseSDK(str(tmp_path / "doc.db"))
        try:
            sdk._get_proj()._upsert_document({
                "id": "doc-a",
                "title": "My doc",
                "document_kind": "brief",
                "summary": "a summary",
                "topics": ["graph"],
                "content": "long body text",
            })
            node = _doc_node(sdk, "doc-a")
            assert node["has_embedding"], "premise: this path embeds"
            assert seen == ["My doc a summary graph"], (
                "the document embedding was not composed from the surface the "
                f"node stores (encoder saw {seen!r}) — the stored `_searchText` "
                f"is {node['searchText']!r} (#8419)")
            assert seen[0] == node["searchText"], (
                "the dense and sparse surfaces disagree on the same node: the "
                f"vector encoded {seen[0]!r} while `_searchText` holds "
                f"{node['searchText']!r} (#8419)")
            assert "long body text" not in seen[0], (
                "the retired `content` prop is still an embedding input")
            assert calls == [{"bounded": True}], (
                "the document fold must carry #13224's write bound: the vector's "
                f"INPUT was reconciled by #8419, the bound is orthogonal and "
                f"both are load-bearing (encoder kwargs seen: {calls!r}). "
                "Dropping it restores an unbounded embedder wait on every "
                "document write, repair and `DocumentCreated` replay.")
            assert node["content"] is None, (
                "premise: D10 does not store `content` — if it ever does, the "
                "objection to embedding it changes and this pin must be redone")
        finally:
            sdk.close()


def test_a_document_with_only_retired_content_earns_no_vector(tmp_path):
    """#8419 indicator 3: `content` alone is not a surface, so it is not text.

    Nothing about it is stored, so a vector derived from it would be
    unreproducible from the graph AND invisible to the FTS leg that searches
    ``_searchText``.
    """
    seen: list[str] = []

    def _encoder(text, _dim=None):
        seen.append(text)
        return _vector(0.0)

    with mock.patch("tortoise.embeddings.encode_for_row", _encoder):
        sdk = TortoiseSDK(str(tmp_path / "doc-content-only.db"))
        try:
            sdk._get_proj()._upsert_document({
                "id": "doc-c", "document_kind": "brief",
                "content": "long body text",
            })
            node = _doc_node(sdk, "doc-c")
            assert seen == [], (
                f"the fold encoded {seen!r} for a document whose only text is "
                "the retired `content` prop (#8419)")
            assert node["has_embedding"] is False
        finally:
            sdk.close()


def test_the_index_path_still_suppresses_the_embedding(tmp_path):
    """The #900 `suppress_embedding` flag is untouched: no encoder call.

    #8419 explicitly does NOT decide whether a document should embed at all —
    only what text it would embed. A fix that started embedding on the index
    path would be that undecided question answered by accident (an unbounded
    network call on every index write).
    """
    seen: list[str] = []

    def _encoder(text, _dim=None):
        seen.append(text)
        return _vector(0.0)

    with mock.patch("tortoise.embeddings.encode_for_row", _encoder):
        sdk = TortoiseSDK(str(tmp_path / "doc-suppressed.db"))
        try:
            sdk._get_proj()._upsert_document({
                "id": "doc-b", "title": "My doc", "document_kind": "brief",
                "summary": "a summary", "topics": ["graph"],
                "content": "long body text", "suppress_embedding": True,
            })
            node = _doc_node(sdk, "doc-b")
            assert seen == [], "the #900 index path started embedding"
            assert node["has_embedding"] is False
            assert node["searchText"] == "My doc a summary graph", (
                "the FTS surface is unchanged by #8419 — only the vector's "
                "input text was reconciled")
        finally:
            sdk.close()
