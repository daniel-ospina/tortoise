"""#3518 — the session ``Source`` FTS index: captured sessions become findable.

Before this change a captured session's ``:Source`` was CREATED (the capture
path ``sdk._materialize_session_source``) but was never FTS-indexed: there was
no ``Source`` label in the full-text index list
(``projection/__init__.py::_ensure_indexes``) and the capture path never wrote
``_searchText``.  ``tortoise_fts_query(entity_type='source')`` therefore
returned nothing — ``index_missing`` on engines whose driver raises for an
absent index, an empty run on engines (like the pinned docker image) that
return an empty result set instead.

These tests drive the READ path (``TortoiseSDK.tortoise_fts_query``) and assert
the captured session is FOUND, not merely that some index row exists.  The
``index_missing`` guard pins the index's existence independently of any query,
so the degradation cannot silently return.

Lane: DOCKER (requires ``TORTOISE_DB_URI``); FTS indexes are gated on
FalkorDB >= 4.x and are not supported by the embedded FalkorDBLite backend.
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001

from tests._live_utils import _skip_unless_live_uri
from tortoise.search_engine import reset_circuit_breakers
from tortoise.sdk import TortoiseSDK

# The capture path's text, and a word from it that appears nowhere else.
CAPTURE_UTTERANCE = "we adopted the QUOKKA migration plan for the ledger"
QUERY_TERM = "QUOKKA"

# The declared field the Source FTS index is created on. Pinned against the
# module constant by `test_source_search_text_field_is_searchtext`; repeated
# here as a literal so the live behaviour tests below fail on their ASSERTIONS
# (not on an import) against the pre-#3518 code, which had neither the index
# nor the constant.
SEARCH_TEXT_FIELD = "_searchText"

#: The session descriptor's DISPLAY prefix (#10000), a literal for the same
#: reason as `SEARCH_TEXT_FIELD`: the assertions below must fail on their own
#: terms, not on an import, against code that predates the change.
DISPLAY_PREFIX = "session capture topics: "


# ── the shared rule (hermetic — no DB) ─────────────────────────────────────


def test_source_search_text_rule() -> None:
    """#3518: ONE rule for a Source's searchable text — title when present,
    else the session's topic lexicon, else summary, else absent (never an empty
    string that pretends to be text)."""
    from tortoise.sdk import _source_search_text

    assert _source_search_text(title="Auth refactor", summary="ignored") == \
        "Auth refactor"
    assert _source_search_text(summary="we adopted QUOKKA") == "we adopted QUOKKA"
    # #10000: the capture path passes its lexicon, and the DISPLAY prefix its
    # `summary` carries must not reach the index (this leg OR-unions).
    assert _source_search_text(
        summary="session capture topics: adopted, quokka",
        topics=["adopted", "quokka"]) == "adopted, quokka"
    assert _source_search_text(summary="ignored", topics=[]) == "ignored"
    assert _source_search_text() is None
    assert _source_search_text(title="", summary="") is None
    assert _source_search_text(summary="", topics=[]) is None


def test_source_search_text_field_is_searchtext() -> None:
    """The declared field is ``_searchText`` — the Document precedent, not a
    second vocabulary (``summary``/``topics`` are deliberately NOT indexed)."""
    from tortoise.sdk import SOURCE_SEARCH_TEXT_FIELD

    assert SOURCE_SEARCH_TEXT_FIELD == SEARCH_TEXT_FIELD


# ── live (docker lane) ─────────────────────────────────────────────────────


@pytest.fixture()
def sdk():
    """A fresh docker graph per test (unique name → no cross-test bleed)."""
    _skip_unless_live_uri()
    reset_circuit_breakers()
    s = TortoiseSDK(graph_name=f"test_source_fts_{uuid.uuid4().hex[:8]}")
    try:
        yield s
    finally:
        reset_circuit_breakers()
        s.close()


def test_captured_session_source_findable_via_fts(sdk) -> None:
    """THE gap this issue closes: search a CAPTURED session's Source and get a
    hit, not ``index_missing``/empty.

    The capture path has no title, so its searchable text is the derived
    summary; the query term occurs only in that text.
    """
    sdk._materialize_session_source(
        "sess-quokka", None, "2026-09-01T10:00:00+00:00",
        [{"role": "user", "content": CAPTURE_UTTERANCE}])

    trace: list[dict] = []
    res = sdk.tortoise_fts_query(
        QUERY_TERM, entity_type="source",
        _elevated_timeout_ms=5000, leg_trace=trace)

    ids = [r["id"] for r in res]
    assert "session:sess-quokka" in ids, (ids, trace)
    # The hit came from the FTS leg — the leg this issue wires — not from a
    # structural/kind scan that would have masked an unindexed source.
    hit = next(r for r in res if r["id"] == "session:sess-quokka")
    assert hit["match_source"] == "fts", hit
    fts = next(e for e in trace if e.get("leg") == "fts")
    assert fts["degraded"] is False, fts
    assert fts["reason"] == "ok", fts
    assert fts["count"] >= 1, fts


def test_index_missing_regression_guard(sdk) -> None:
    """The ``index_missing`` degradation cannot silently return: the ``Source``
    full-text index on ``_searchText`` EXISTS after projection boot.

    Asserted against ``db.indexes()`` (not a query) so it holds whatever a
    given engine returns for a query against an absent index — the failure
    mode this issue fixes was silent emptiness on the pinned docker image.
    """
    rows = sdk._get_proj().g.query("CALL db.indexes()").result_set
    by_label = {r[0]: (list(r[1]), r[2]) for r in rows}
    assert "Source" in by_label, f"no Source index at all: {sorted(by_label)}"
    fields, types = by_label["Source"]
    assert SEARCH_TEXT_FIELD in fields, (fields, types)
    assert "FULLTEXT" in types[SEARCH_TEXT_FIELD], types


def test_capture_and_indexer_source_field_parity(sdk) -> None:
    """Both Source writers populate the SAME searchable field through the SAME
    rule, and both are findable — so the capture path and the indexer path can
    never drift into different vocabularies."""
    from tortoise.sdk import _source_search_text

    # 1) capture path (sdk._materialize_session_source) — no title, so the
    #    derived summary is the searchable text. Since #10000 that summary is the
    #    session's TOPIC LEXICON, never the turn's own wording (§9.1/D1).
    sdk._materialize_session_source(
        "sess-parity", None, "2026-09-01T10:00:00+00:00",
        [{"role": "user", "content": CAPTURE_UTTERANCE}])
    # 2) indexer path — exactly what `_index_source_merge` calls (the write
    #    that `index_directory` performs), title-carried.
    sdk._index_source_merge(
        "corpus://parity/s1.md", "agentSession", "2026-09-01",
        "hash-parity-1", "PARITYPHRASE auth refactor session",
        "/tmp/s1.md", gate_v=None)

    g = sdk._get_proj().g
    rows = dict(g.query(
        "MATCH (s:Source) RETURN s.url, s._searchText").result_set)
    summaries = dict(g.query(
        "MATCH (s:Source) RETURN s.url, s.summary").result_set)
    topics = dict(g.query(
        "MATCH (s:Source) RETURN s.url, s.topics").result_set)
    # #10000 (§9.1/D1): the capture's node carries a topic descriptor, NOT the
    # session's own turn text — and because `_searchText` is that same
    # derivation, the prose cannot survive in the FTS index after leaving the
    # graph. Asserted here as an absence so a future writer cannot restore the
    # prose route and still pass on "both writers agree".
    assert CAPTURE_UTTERANCE not in (summaries["session:sess-parity"] or ""), \
        summaries
    assert rows["corpus://parity/s1.md"] == "PARITYPHRASE auth refactor session", \
        rows

    # Both resolved through the shared rule (the parity pin) — the capture arm is
    # resolved from the node's OWN stored `topics`, so this stays a statement
    # about the RULE and not about which prose the derivation happened to emit.
    assert rows["session:sess-parity"] == _source_search_text(
        topics=topics["session:sess-parity"])
    assert rows["corpus://parity/s1.md"] == _source_search_text(
        title="PARITYPHRASE auth refactor session")

    # #10000: `summary` is the DISPLAY form of the SAME lexicon the index holds,
    # so the two derived columns stay a function of one another — and the prefix
    # that makes the display readable is exactly what the index must not carry.
    assert summaries["session:sess-parity"] == (
        DISPLAY_PREFIX + rows["session:sess-parity"])
    assert DISPLAY_PREFIX.strip() not in rows["session:sess-parity"]

    # …and both are actually findable through the read path.
    for term, url in (("QUOKKA", "session:sess-parity"),
                      ("PARITYPHRASE", "corpus://parity/s1.md")):
        res = sdk.tortoise_fts_query(
            term, entity_type="source", _elevated_timeout_ms=5000)
        assert url in [r["id"] for r in res], (term, res)
