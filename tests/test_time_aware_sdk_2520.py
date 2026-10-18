"""#2520 (C6) — the SDK's query-side date anchor on the DENSE leg.

Class B, per test:

  (1) **What value makes this test fail?** — named in each docstring.
  (2) **Is the value reachable?** — the fixture installs a RECORDING
      embedder, so the exact string handed to ``model.encode`` is
      observable; the failing value (the bare, un-anchored query) is the
      default behaviour and is asserted as the negative control.

The recording embedder makes the test lane-independent (no model load, no
network, no `embeddings` extra needed) while still exercising the real
``tortoise_fts_query`` control flow. Requires a live FalkorDB for the SDK
graph (docker lane) — the anchor decision itself is pure.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tests import _live_utils
from tortoise.sdk import TortoiseSDK


def _falkordb_available() -> bool:
    """Probe a live FalkorDB; reads TORTOISE_DB_URI at CALL time."""
    uri = os.environ.get(
        "TORTOISE_DB_URI",  # empty-uri-default-ok: #2815 skip declared; converting flips the tier-2 lane
        _live_utils.docker_uri("tortoise_test_matrix")).rstrip("/")
    old = os.environ.get("TORTOISE_DB_URI")
    try:
        os.environ["TORTOISE_DB_URI"] = f"{uri}_probe2520sdk"
        from tortoise.sdk import TortoiseSDK as _ProbeSDK
        _probe = _ProbeSDK()
        _probe._get_proj().g.query("RETURN 1")
        _probe.close()
        return True
    except Exception:
        return False
    finally:
        if old is not None:
            os.environ["TORTOISE_DB_URI"] = old
        else:
            os.environ.pop("TORTOISE_DB_URI", None)


FALKORDB_AVAILABLE = _falkordb_available()

#: The graph-backed tests need a live FalkorDB; the pure anchor-decision
#: tests below are hermetic and always run — so the SDK-side contract is
#: never permanently skipped in CI (a lane without FalkorDB still proves
#: the decision; the graph half proves the wiring).
_DOCKER_ONLY = pytest.mark.skipif(
    not FALKORDB_AVAILABLE,
    reason="requires TORTOISE_DB_URI (live FalkorDB lane)")


class _RecordingEmbedder:
    """A stand-in for ``EmbeddingModel`` that records every encoded text."""

    def __init__(self) -> None:
        self.texts: list[list[str]] = []

    def encode(self, texts):
        self.texts.append(list(texts))
        return [[0.0] * 384 for _ in texts]


@pytest.fixture()
def recording_embedder(monkeypatch):
    """Install the recording embedder and pin out write-time embedding."""
    import tortoise.embeddings as emb
    recorder = _RecordingEmbedder()
    monkeypatch.setattr(emb.EmbeddingModel, "get",
                        staticmethod(lambda: recorder))
    monkeypatch.setattr(emb, "compute_embedding",
                        staticmethod(lambda content: None))
    return recorder


@pytest.fixture()
def sdk():
    uri = (os.environ.get(
        "TORTOISE_DB_URI",  # empty-uri-default-ok: #2815 skip declared; converting flips the tier-2 lane
        _live_utils.docker_uri("tortoise_test_matrix")).rstrip("/")
        + "_" + uuid.uuid4().hex[:10])
    old = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = uri
    sdk = TortoiseSDK(namespace="test_time_aware_2520")
    yield sdk
    sdk.close()
    if old is not None:
        os.environ["TORTOISE_DB_URI"] = old
    else:
        os.environ.pop("TORTOISE_DB_URI", None)


def _encoded(recorder) -> list[str]:
    return [t for call in recorder.texts for t in call]


@_DOCKER_ONLY
def test_off_path_embeds_the_bare_query(recording_embedder, sdk):
    """FAIL VALUE: any anchor on the off path — the default-off contract is
    byte-identical. Reachable: ``time_aware`` defaults to False."""
    sdk.tortoise_fts_query("where do I live now?", limit=5)
    assert "where do I live now?" in _encoded(recording_embedder)
    assert not any("(as of" in t for t in _encoded(recording_embedder))


@_DOCKER_ONLY
def test_time_aware_anchors_the_dense_query(recording_embedder, sdk):
    """FAIL VALUE: the bare query reaching ``model.encode`` when the arm is
    ON with a valid date. Reachable: the recorder captures the exact
    string; a no-op injection fails the assertion."""
    sdk.tortoise_fts_query(
        "where do I live now?", limit=5,
        time_aware=True, query_date="2026-09-25")
    assert "where do I live now? (as of 2026-09-25)" in _encoded(
        recording_embedder)


@_DOCKER_ONLY
def test_date_pinned_query_is_not_anchored(recording_embedder, sdk):
    """FAIL VALUE: an anchor on a DATE-PINNED query — the invert-recency
    guard. Reachable: the query names a year, so the detector returns
    DATE_PINNED and the bare query must be embedded."""
    sdk.tortoise_fts_query(
        "where did I live in 2024?", limit=5,
        time_aware=True, query_date="2026-09-25")
    assert "where did I live in 2024?" in _encoded(recording_embedder)
    assert not any("(as of" in t for t in _encoded(recording_embedder))


@_DOCKER_ONLY
def test_time_aware_without_a_date_is_a_noop(recording_embedder, sdk):
    """FAIL VALUE: an invented anchor when ``query_date`` is absent.
    Reachable: ``query_date`` defaults to None."""
    sdk.tortoise_fts_query(
        "where do I live now?", limit=5, time_aware=True)
    assert "where do I live now?" in _encoded(recording_embedder)
    assert not any("(as of" in t for t in _encoded(recording_embedder))


@_DOCKER_ONLY
def test_fts_leg_keeps_the_original_query(recording_embedder, sdk):
    """FAIL VALUE: the sparse leg being handed the anchored string (date
    tokens dilute the OR-union) — the anchor is a DENSE-leg concern only.
    Reachable: the recorder proves the dense leg got the anchor; this test
    pins that a second, un-anchored embed never appears, i.e. the anchor is
    not re-encoded elsewhere."""
    sdk.tortoise_fts_query(
        "where do I live now?", limit=5,
        time_aware=True, query_date="2026-09-25")
    encoded = _encoded(recording_embedder)
    assert encoded.count("where do I live now? (as of 2026-09-25)") == 1
    assert "where do I live now?" not in encoded


# ── hermetic half (no DB, no embedder): the factored anchor decision ──────

def test_dense_query_decision_is_lane_independent():
    """FAIL VALUE: the anchor decision depending on a live graph. The SDK
    factors the decision into ``tortoise.time_aware.dense_query_for``, so
    this half runs on any lane — a FalkorDB-less CI still proves the
    contract; the graph half above proves the wiring.

    Reachable: every branch (armed/off, prefer-latest/date-pinned/None-date)
    is a pure function call."""
    from tortoise.time_aware import dense_query_for
    assert dense_query_for("where do I live now?", time_aware=True,
                           query_date="2026-09-25") == (
        "where do I live now? (as of 2026-09-25)")
    assert dense_query_for("where do I live now?", time_aware=False,
                           query_date="2026-09-25") == "where do I live now?"
    assert dense_query_for("where did I live in 2024?", time_aware=True,
                           query_date="2026-09-25") == (
        "where did I live in 2024?")
    assert dense_query_for("where do I live now?", time_aware=True,
                           query_date=None) == "where do I live now?"


# ══════════════════════════════════════════════════════════════════════════
# #8487 — the read path consumes valid time (rank-time selection)
# ══════════════════════════════════════════════════════════════════════════
#
# The measured defect (re-measured on the shipped read path 2026-10-18): a
# three-claim knowledge-update graph returned the ENDED claim and a
# NOT-YET-EFFECTIVE claim inside the page as current facts, because nothing
# in `tortoise_fts_query` consumed `validFrom`/`validTo`. The rules are
# pinned hermetically in `test_time_aware_2520.py`; these tests prove the
# WIRING (pre-truncation, membership-preserving, kill-switchable, traced).
#
# ISOLATION: this file's `sdk` fixture pins a fixed namespace, so its graph
# accumulates rows across tests. These tests build their own graph per test
# (unique namespace) — otherwise a pool larger than `limit` makes the WRITE
# pre-truncation behaviour (which is the point of the change) look like a
# membership change from a sibling test's leftovers.

_ENDED = "The production database for the billing ledger is Postgres 14"
_CURRENT = "The production database for the billing ledger is Postgres 16"
_FUTURE = "The production database for the billing ledger is Postgres 17"
#: A query with NO temporal intent — the byte-identical control.
_PLAIN_QUERY = "production database billing ledger Postgres"
#: A query that asks what is true NOW.
_PRESENT_QUERY = "what production database do we use for the billing ledger now?"


@pytest.fixture()
def ku_graph(recording_embedder):
    """The knowledge-update shape: ended + current + not-yet-effective.

    Windows are stamped with the SAME properties the real writers write
    (`supersede_point`/`invalidate_point` stamp `validFrom`/`validTo`), so the
    read path sees a production-shaped row — not a test-only field.
    """
    uri = (os.environ.get(
        "TORTOISE_DB_URI",  # empty-uri-default-ok: #2815 skip declared
        _live_utils.docker_uri("tortoise_test_matrix")).rstrip("/")
        + "_" + uuid.uuid4().hex[:10])
    old = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = uri
    sdk = TortoiseSDK(namespace="ku8487_" + uuid.uuid4().hex[:8])
    try:
        ids = {}
        for name, text in (("ended", _ENDED), ("current", _CURRENT),
                           ("future", _FUTURE)):
            ids[name] = sdk.create_point("statement", text, status="live")["id"]
        g = sdk._get_proj().g
        g.query("MATCH (n:Point {id:$id}) "
                "SET n.validFrom='2021-01-01', n.validTo='2023-06-01'",
                params={"id": ids["ended"]})
        g.query("MATCH (n:Point {id:$id}) SET n.validFrom='2023-06-02'",
                params={"id": ids["current"]})
        g.query("MATCH (n:Point {id:$id}) SET n.validFrom='2099-01-01'",
                params={"id": ids["future"]})
        assert g.query("MATCH (n:Point) RETURN count(n)"
                       ).result_set[0][0] == 3, "fixture graph must hold exactly 3 points"
        yield sdk, ids
    finally:
        sdk.close()
        if old is not None:
            os.environ["TORTOISE_DB_URI"] = old
        else:
            os.environ.pop("TORTOISE_DB_URI", None)


def _order(sdk, query, **kwargs) -> list[str]:
    return [h["id"] for h in sdk.tortoise_fts_query(query, limit=10, **kwargs)]


def _valid_time_entry(trace) -> dict:
    entries = [e for e in trace if e.get("leg") == "valid_time"]
    assert len(entries) == 1, f"expected exactly one valid_time entry, got {trace}"
    return entries[0]


@_DOCKER_ONLY
def test_present_tense_query_demotes_the_ended_claim(ku_graph):
    """FAIL VALUE: the pre-#8487 order — the ended claim keeps its relevance
    rank. Reachable: with the selection OFF the same graph returns the ended
    claim at its relevance rank (measured: rank 0), which the ON path must
    move below the current claim."""
    sdk, ids = ku_graph
    off = _order(sdk, _PRESENT_QUERY, _valid_time_rerank=False)
    on = _order(sdk, _PRESENT_QUERY)
    assert sorted(on) == sorted(off)          # membership preserved: no filter
    assert on.index(ids["ended"]) > off.index(ids["ended"])
    # The demoted group is exactly the two non-current claims and it sits
    # BELOW the current one (its own internal order is never re-ranked).
    assert on[0] == ids["current"]
    assert set(on[-2:]) == {ids["ended"], ids["future"]}


@_DOCKER_ONLY
def test_not_yet_effective_claim_is_not_served_as_current(ku_graph):
    """FAIL VALUE: a 2099-dated claim ranked ahead of the current one on a
    "what do we use NOW" question. Reachable: without the selection the
    future claim can outrank the current one on lexical grounds."""
    sdk, ids = ku_graph
    on = _order(sdk, _PRESENT_QUERY)
    assert on.index(ids["current"]) < on.index(ids["future"])


@_DOCKER_ONLY
def test_plain_query_keeps_pure_relevance_order(ku_graph):
    """FAIL VALUE: reordering a query with no temporal intent (the
    byte-identical contract for topical reads). Reachable: the same graph +
    the ON path."""
    sdk, _ = ku_graph
    trace: list[dict] = []
    plain = _order(sdk, _PLAIN_QUERY, leg_trace=trace)
    assert plain == _order(sdk, _PLAIN_QUERY, _valid_time_rerank=False)
    entry = _valid_time_entry(trace)
    assert entry["ran"] is True and entry["degraded"] is False
    assert entry["reason"] == "no-prefer-latest-intent"
    assert entry["count"] == 0


@_DOCKER_ONLY
def test_the_trace_names_what_was_demoted(ku_graph):
    """FAIL VALUE: an un-auditable reorder (#8487 scope 3: the returned fact
    must be traceable to the version selected and why). Reachable: the
    present-tense read on the seeded graph."""
    sdk, _ = ku_graph
    trace: list[dict] = []
    _order(sdk, _PRESENT_QUERY, leg_trace=trace)
    entry = _valid_time_entry(trace)
    assert entry["reason"] == "latest-first"
    assert entry["count"] == 2                # ended + not-yet-effective


@_DOCKER_ONLY
def test_env_kill_switch_restores_relevance_order(ku_graph, monkeypatch):
    """FAIL VALUE: an operator's explicit OFF not honoured. Reachable: the
    same read that DOES reorder when the knob is unset."""
    sdk, _ = ku_graph
    monkeypatch.setenv("TORTOISE_VALID_TIME_RERANK", "0")
    assert _order(sdk, _PRESENT_QUERY) == _order(
        sdk, _PRESENT_QUERY, _valid_time_rerank=False)


@_DOCKER_ONLY
def test_terminal_inclusion_still_gets_the_selection(ku_graph):
    """The ask lane reads with ``include_terminal=True`` by design (the reader
    discounts by marker), so terminal exclusion cannot be what orders these
    rows. FAIL VALUE: the selection skipped on that read shape. Reachable:
    ``include_terminal=True`` is exactly the ask lane's call."""
    sdk, ids = ku_graph
    on = _order(sdk, _PRESENT_QUERY, include_terminal=True)
    off = _order(sdk, _PRESENT_QUERY, include_terminal=True,
                 _valid_time_rerank=False)
    assert sorted(on) == sorted(off)          # still a reorder, never a filter
    assert on.index(ids["ended"]) >= off.index(ids["ended"])
    assert on[0] == ids["current"]
