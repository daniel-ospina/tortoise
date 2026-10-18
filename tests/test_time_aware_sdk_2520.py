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
from contextlib import contextmanager
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


#: The same claim, phrased so it OUTRANKS the current one on relevance (it
#: carries the query's own "we use … now" words in a SHORT form). Needed by the
#: one test that must observe the not-yet-effective demotion: in the default
#: fixture that claim already ranks last, so its demotion is invisible (see
#: ``test_not_yet_effective_claim_is_not_served_as_current``).
#:
#: MEASURED, not assumed (#8487 review P2): the recording embedder makes the
#: dense leg fail (the mock returns lists, the vector leg wants ``.tolist()``),
#: so relevance here IS the FalkorDB FTS/BM25 score — length-normalised. A
#: LONGER phrasing that merely repeats the query's words therefore ranks
#: LOWER (measured: "We use the production database now for the billing
#: ledger: Postgres 17" scored 0.61 vs the current claim's 1.17 and came
#: back LAST). This shorter form carries the same words and wins the
#: BM25 comparison (measured: future first).
_FUTURE_OUTRANKING = (
    "the production database billing ledger we use now is Postgres 17")


@contextmanager
def _ku_sdk(future_text: str = _FUTURE):
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
    sdk = TortoiseSDK(namespace="test_ku8487_" + uuid.uuid4().hex[:8])
    try:
        ids = {}
        for name, text in (("ended", _ENDED), ("current", _CURRENT),
                           ("future", future_text)):
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


@pytest.fixture()
def ku_graph(recording_embedder):
    with _ku_sdk() as pair:
        yield pair


@pytest.fixture()
def ku_graph_future_first(recording_embedder):
    """Same shape, but the not-yet-effective claim leads relevance.

    The default fixture's relevance order already ends with the future claim,
    so no assertion about ITS position can distinguish a selection from none.
    This variant puts it FIRST, which is the only shape in which "the future
    claim is not served as current" is a claim the read path has to earn.
    """
    with _ku_sdk(_FUTURE_OUTRANKING) as pair:
        yield pair


def _order(sdk, query, *, limit=10, **kwargs) -> list[str]:
    return [h["id"] for h in sdk.tortoise_fts_query(query, limit=limit,
                                                    **kwargs)]


def _hit(hits, pid) -> dict:
    found = [h for h in hits if h["id"] == pid]
    assert len(found) == 1, f"expected exactly one hit for {pid}"
    return found[0]


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
def test_not_yet_effective_claim_is_not_served_as_current(ku_graph_future_first):
    """FAIL VALUE: a 2099-dated claim served ahead of the current one on a
    "what do we use NOW" question.

    Reachable, and PROVEN non-vacuous: the fixture is built so that claim
    OUTRANKS the current one on relevance, and the OFF baseline below pins that
    fact. Without both halves the assertion would hold on pure relevance order
    and prove nothing (the finding the review made against the first cut of
    this test)."""
    sdk, ids = ku_graph_future_first
    off = _order(sdk, _PRESENT_QUERY, _valid_time_rerank=False)
    # The precondition the assertion needs: relevance alone puts the
    # not-yet-effective claim FIRST, i.e. it IS served as current pre-#8487.
    assert off.index(ids["future"]) < off.index(ids["current"]), (
        f"fixture precondition broken: {off}")
    on = _order(sdk, _PRESENT_QUERY)
    assert sorted(on) == sorted(off)          # membership preserved: no filter
    assert on.index(ids["current"]) < on.index(ids["future"])
    assert on[0] == ids["current"]


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
    ``include_terminal=True`` is exactly the ask lane's call. STRICT: ``>``, not
    ``>=`` — a non-strict compare also passes when nothing moved."""
    sdk, ids = ku_graph
    on = _order(sdk, _PRESENT_QUERY, include_terminal=True)
    off = _order(sdk, _PRESENT_QUERY, include_terminal=True,
                 _valid_time_rerank=False)
    assert sorted(on) == sorted(off)          # still a reorder, never a filter
    assert off.index(ids["ended"]) < off.index(ids["current"]), (
        f"fixture precondition broken: {off}")
    assert on.index(ids["ended"]) > off.index(ids["ended"])
    assert on[0] == ids["current"]


@_DOCKER_ONLY
def test_the_selection_runs_before_the_limit_cut(ku_graph):
    """THE headline property (#8487): the selection happens on the
    pre-truncation fused pool, so ``limit`` counts SELECTED candidates.

    FAIL VALUE: the selection applied AFTER ``result_ids = result_ids[:limit]``
    — the one placement the change exists for and, before this test, the one
    placement no test could see (every other wiring test uses ``limit=10`` on a
    3-point graph, where the cut is a no-op).

    Reachable: at ``limit=1`` the pool (3 ids) exceeds the page, relevance puts
    the ENDED claim first — so a post-cut reorder can only ever return that
    same 1 row, while a pre-cut reorder returns the current claim.
    """
    sdk, ids = ku_graph
    off = _order(sdk, _PRESENT_QUERY, limit=1, _valid_time_rerank=False)
    assert off == [ids["ended"]], (
        f"fixture precondition broken: relevance must lead with the ended "
        f"claim at limit=1, got {off}")
    on = _order(sdk, _PRESENT_QUERY, limit=1)
    assert on == [ids["current"]], (
        "the current claim must win the single page slot — a selection applied "
        "after the cut cannot put it there")


@_DOCKER_ONLY
def test_a_short_state_map_records_the_demotions_it_did_make(ku_graph,
                                                            monkeypatch):
    """FAIL VALUE: a NON-EMPTY but SHORT state map recording ``count=0`` /
    "no selection" on a read whose page order it demonstrably moved (#8487
    review round 6, P2).

    Completeness decides REUSE (``_valid_time_state``, so step 7.6 re-reads),
    not whether the selection runs: a partial map still carries the real state
    of the ids it holds, so the demotion of those ids is genuinely applied —
    and the audit leg must report it instead of claiming "nothing happened"
    (#8487 scope 3). The empty-map case the sibling test covers cannot see this,
    because there the selection is skipped entirely.

    Reachable: the FIRST state read of the query is made to cover exactly ONE
    of the three pool ids — the ended claim, the one whose state map is
    load-bearing (``is_stale_entry`` needs it to demote).
    """
    sdk, ids = ku_graph
    import tortoise.search_engine as _se
    real = _se.fetch_point_epistemic_state
    calls: list[int] = []

    def _short_then_real(graph, point_ids):
        calls.append(len(point_ids))
        if len(calls) == 1:
            # PARTIAL, not empty: only the ended claim's real state comes back.
            return real(graph, [ids["ended"]])
        return real(graph, point_ids)

    monkeypatch.setattr(_se, "fetch_point_epistemic_state", _short_then_real)
    trace: list[dict] = []
    on = _order(sdk, _PRESENT_QUERY, leg_trace=trace)
    # The selection DID run over that partial map, and its ordering reached the
    # page: the ended claim (relevance rank 0 — the sibling test's measured
    # precondition) is demoted.
    assert on[-1] == ids["ended"], (
        f"a short map still demotes the ids it holds: {on}")
    entry = _valid_time_entry(trace)
    assert entry["count"] == 1, (
        "the audit leg must count the demotions it MADE, not the completeness "
        f"of the map; got {entry}")
    assert entry["reason"] == "state_read_incomplete", (
        "the reason still has to say the map was short — that is what made "
        f"step 7.6 re-read; got {entry}")
    assert entry["degraded"] is False
    # …and the reuse half is unaffected: a short map is never reused as the
    # page's decoration, so every hit keeps its window.
    ended = _hit(sdk.tortoise_fts_query(_PRESENT_QUERY, limit=10), ids["ended"])
    assert ended["valid_to"] == "2023-06-01"


@_DOCKER_ONLY
def test_a_failed_state_read_does_not_strip_the_page_decoration(ku_graph,
                                                              monkeypatch):
    """FAIL VALUE: an EMPTY state map from step 5e replayed as the page's
    epistemic decoration (#8487 review P1).

    ``fetch_point_epistemic_state`` never raises — it swallows the failure and
    returns ``{}`` — and ``{} is not None``, so caching on ``is not None`` left
    step 7.6 with nothing to do: every hit lost ``status`` / ``valid_from`` /
    ``valid_to`` / ``end_state``, which means a superseded claim was rendered
    WITHOUT its ``[SUPERSEDED BY]`` marker and window, while the audit leg
    still recorded a clean selection.

    Reachable: the first state read of the query is made to fail; the page-wide
    read that step 7.6 falls back to is the real one.
    """
    sdk, ids = ku_graph
    import tortoise.search_engine as _se
    real = _se.fetch_point_epistemic_state
    calls: list[int] = []

    def _first_read_fails(graph, point_ids):
        calls.append(len(point_ids))
        return {} if len(calls) == 1 else real(graph, point_ids)

    monkeypatch.setattr(_se, "fetch_point_epistemic_state", _first_read_fails)
    trace: list[dict] = []
    hits = sdk.tortoise_fts_query(_PRESENT_QUERY, limit=10, leg_trace=trace)
    ended = _hit(hits, ids["ended"])
    assert ended["status"] == "live", "the point's status decoration was lost"
    assert ended["valid_from"] == "2021-01-01"
    assert ended["valid_to"] == "2023-06-01", (
        "the closed window is what renders [SUPERSEDED BY]; an empty cached map "
        "serves the ended claim as current")
    assert ended["end_state"] == "known"
    assert len(calls) >= 2, (
        "step 7.6 must fetch its own page state when 5e's map is unusable")
    entry = _valid_time_entry(trace)
    assert entry["degraded"] is False, (
        "a short state read is an ordering impairment, NOT a degraded read: "
        "``degraded`` in a leg trace means a retrieval leg did not run, and "
        "ask_lane/read_status fold ANY such entry into the caller-facing "
        "degradation signal — reddening a healthy read")
    assert entry["reason"] == "state_read_incomplete"
    # ``degraded=False`` on this leg is the whole property: the caller-facing
    # degradation consumers fold on the FLAG (``ask_lane``'s
    # ``any(leg.get("degraded"))`` and ``read_status.classify_leg_trace``), so
    # a flag that is False here cannot contribute to their signal. Checked at
    # the classifier, which is the consumer that was actually observable.
    from tortoise.read_status import STATUS_DEGRADED, classify_leg_trace
    assert classify_leg_trace([entry], hit_count=len(hits)) != STATUS_DEGRADED, (
        "read_status must not call this read degraded — the hits are complete")


@_DOCKER_ONLY
def test_a_raised_selection_records_its_own_reason(ku_graph, monkeypatch):
    """FAIL VALUE: a RAISED selection recorded as ``state_read_incomplete``,
    i.e. the audit leg unable to tell a selection that blew up from a state
    read that came back short (#8487 review P2, secondary).

    Reachable: ``prefer_latest_ids`` raising is the exception branch of step
    5e; it is fail-open by design, so the read must still succeed.
    """
    sdk, _ = ku_graph
    import tortoise.time_aware as _ta

    def _boom(*args, **kwargs):
        raise RuntimeError("selection exploded")

    monkeypatch.setattr(_ta, "prefer_latest_ids", _boom)
    trace: list[dict] = []
    hits = sdk.tortoise_fts_query(_PRESENT_QUERY, limit=10, leg_trace=trace)
    assert hits, "a raised selection must never fail the read (fail-open)"
    entry = _valid_time_entry(trace)
    assert entry["reason"] == "selection_failed"
    assert entry["degraded"] is False


@_DOCKER_ONLY
def test_a_raised_state_read_is_not_recorded_as_a_selection_failure(
        ku_graph, monkeypatch):
    """FAIL VALUE: one shared handler recording EVERY exception of step 5e as
    ``selection_failed`` (#8487 review round 5, P3).

    The state read and the selection are separate reads; the audit leg exists so
    a reader can tell WHICH one failed. ``fetch_point_epistemic_state`` swallows
    its own exceptions, so the seam is patched to raise — the belt-and-braces
    branch — and the entry must say ``state_read_failed``, not "a selection
    blew up".

    Reachable: the raised branch of the fetch seam; the read must stay
    fail-open and the page must still be decorated by step 7.6's own read.
    """
    sdk, ids = ku_graph
    import tortoise.search_engine as _se
    real = _se.fetch_point_epistemic_state
    calls: list[int] = []

    def _first_raises(graph, point_ids):
        calls.append(len(point_ids))
        if len(calls) == 1:
            raise RuntimeError("state read exploded")
        return real(graph, point_ids)

    monkeypatch.setattr(_se, "fetch_point_epistemic_state", _first_raises)
    trace: list[dict] = []
    hits = sdk.tortoise_fts_query(_PRESENT_QUERY, limit=10, leg_trace=trace)
    assert hits, "a raised state read must never fail the read (fail-open)"
    ended = _hit(hits, ids["ended"])
    assert ended["valid_to"] == "2023-06-01", (
        "step 7.6 must still decorate the page after 5e's read raised")
    entry = _valid_time_entry(trace)
    assert entry["reason"] == "state_read_failed"
    assert entry["degraded"] is False


@_DOCKER_ONLY
def test_a_failed_page_state_read_is_recorded_where_it_happened(
        ku_graph, monkeypatch):
    """FAIL VALUE: NO leg-trace record at all when the PAGE state read fails
    (#8487 review round 5, P3).

    ``fetch_point_epistemic_state`` never raises — it returns ``{}`` — so the
    page is served without ``status`` / ``valid_to`` / ``[SUPERSEDED BY]`` and
    the read still classifies as healthy. Before round 5 the ordering leg
    accidentally witnessed this; now that it reports ``degraded=False``
    (an ordering impairment is not a degradation), the read that actually
    failed must record it — ``epistemic_state`` with ``degraded=True``, which
    is exactly what that flag means.

    Reachable: EVERY state read of the query returns ``{}``.
    """
    sdk, _ = ku_graph
    import tortoise.search_engine as _se

    monkeypatch.setattr(_se, "fetch_point_epistemic_state",
                        lambda *a, **k: {})
    trace: list[dict] = []
    hits = sdk.tortoise_fts_query(_PRESENT_QUERY, limit=10, leg_trace=trace)
    assert hits, "an undecorated page is still a page"
    page_entries = [e for e in trace if e.get("leg") == "epistemic_state"]
    assert len(page_entries) == 1, (
        f"the failed page read must be recorded exactly once, got {trace}")
    assert page_entries[0]["degraded"] is True
    assert page_entries[0]["reason"] == "state_read_failed"
    from tortoise.read_status import STATUS_DEGRADED, classify_leg_trace
    assert classify_leg_trace(trace, hit_count=len(hits)) == STATUS_DEGRADED, (
        "an undecorated page IS a degraded read — the decoration leg did not run")


@_DOCKER_ONLY
def test_a_topical_query_pays_no_pool_width_state_read(ku_graph, monkeypatch):
    """FAIL VALUE: the pool-width epistemic-state read issued for a query with
    NO temporal intent (#8487 review P2).

    The intent gate lives inside ``prefer_latest_ids``, i.e. after the read, so
    the read fired on every point/relevance read and its result was discarded.
    The observation is the ID SET the read is asked for: the pool (3 ids) when
    the gate runs late, the page (``limit``=2 ids) when it runs first.
    """
    sdk, _ = ku_graph
    import tortoise.search_engine as _se
    real = _se.fetch_point_epistemic_state
    seen: list[int] = []

    def _counting(graph, point_ids):
        seen.append(len(point_ids))
        return real(graph, point_ids)

    monkeypatch.setattr(_se, "fetch_point_epistemic_state", _counting)
    sdk.tortoise_fts_query(_PLAIN_QUERY, limit=2)
    assert seen == [2], (
        "a topical query must only pay the PAGE-width state read that step 7.6 "
        f"always did; saw reads over {seen} ids")


# ── the internal lanes that RE-OWN the order downstream (#8487 review) ─────
#
# The selection's *ordering* reaches only a caller that keeps it. A lane that
# re-sorts the pool itself (StateRanker / GapsRanker / _issue_insight_rank / a
# confidence band) discards the ordering while still inheriting its other half
# — the pre-cut demotion, i.e. which ids survive the pool — so leaving the
# selection on there is an unmeasured behaviour change to a product surface.
# The eval lane's opt-out (`tools/longmem_eval/retrieve.py`) is the precedent;
# these lanes must carry the same flag.
#
# Two MORE lanes of that shape were found in review round 6 and were NOT opted
# out: `_review_pool` (a MEMBERSHIP filter for `review_add` / `review_prune`,
# both of which order it themselves) and `_resolve_subgraph_seed` (an expansion
# SEED SET handed to `SubgraphExpander.expand`). Neither keeps the read order,
# so for both the only remaining effect is which ids survive the cut — the same
# composition change the eval lane is protected from.

_RE_OWNING_LANES = (
    # (label, method name, args, kwargs)
    ("recall_state", "recall_state", (_PLAIN_QUERY,), {}),
    ("issue_insight", "issue_insight", ("billing ledger", "the database"), {}),
    ("suggest_entry_points", "suggest_entry_points", ("billing ledger",), {}),
    ("recall_gaps", "recall_gaps", (_PLAIN_QUERY,), {}),
    # #8487 review round 6, P2: the review pool is a MEMBERSHIP filter and the
    # subgraph seed is an expansion START SET — neither consumes the order.
    ("_review_pool", "_review_pool", ("billing ledger now",), {}),
    ("_resolve_subgraph_seed", "_resolve_subgraph_seed",
     ("billing ledger now",), {}),
)


@_DOCKER_ONLY
def test_internal_pool_reads_opt_out_of_the_selection(sdk, monkeypatch):
    """FAIL VALUE: a re-owned-order pool read still paying the SDK-side
    selection (#8487 review P2).

    Reachable: the product methods are called for real with the retrieval call
    replaced by a recording stub, so the kwargs they pass are exactly the ones
    a live call would pass. The stub returns [] — every lane tolerates an empty
    pool, so the call reaches the retrieval site either way.
    """
    seen: list[tuple[str, dict]] = []

    def _record(*args, **kwargs):
        seen.append((args[0] if args else "", kwargs))
        return []

    monkeypatch.setattr(sdk, "tortoise_fts_query", _record)
    failures = []
    for label, method, args, kwargs in _RE_OWNING_LANES:
        seen.clear()
        getattr(sdk, method)(*args, **kwargs)
        if not seen:
            failures.append(f"{label}: never reached tortoise_fts_query")
            continue
        for _q, kw in seen:
            if kw.get("_valid_time_rerank") is not False:
                failures.append(
                    f"{label}: pool read without _valid_time_rerank=False "
                    f"(kwargs: {sorted(kw)})")
    assert not failures, "\n".join(failures)


def test_the_measurement_lanes_declare_their_valid_time_choice():
    """FAIL VALUE: a `tools/` lane that grades retrieval ORDER silently moving
    with the #8487 selection (or a mirror lane silently opting out and so
    measuring a reader the product does not run).

    Reachable: an AST pass over the six declared lanes below, which asserts the
    DECLARED
    choice per lane rather than a general rule — each file's reason is recorded
    at its own call site or in its own docstring. Only `sdk.tortoise_fts_query`
    call sites are inspected, so unrelated calls cannot satisfy it.
    """
    import ast

    root = Path(__file__).resolve().parent.parent
    # lane -> expected value of `_valid_time_rerank` (None = must NOT be passed)
    expected: dict[str, bool | None] = {
        # Re-own the order / must stay comparable to recorded numbers:
        "tools/fusion_formula_ab.py": False,
        "tools/ask_pool_admission_probe.py": False,
        "tools/longmem_eval/retrieve.py": False,
        # #8487 review round 7, P2: the capture-time prior leg re-owns the
        # order (rows accumulate into id-keyed dicts, consumed as a SET).
        "tortoise/extractor_v2.py": False,
        # Mirror the ask lane: must measure/replay the shipped reader.
        "tools/ask_recall_bench.py": None,
        "tools/gen_ask_transcripts.py": None,
    }

    def _declared(path: Path) -> list[bool | None]:
        tree = ast.parse(path.read_text())
        out: list[bool | None] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "attr", getattr(fn, "id", ""))
            if name != "tortoise_fts_query":
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            value = kw.get("_valid_time_rerank")
            if value is None:
                out.append(None)
            elif isinstance(value, ast.Constant):
                out.append(value.value)
            else:
                out.append("unexpected-expression")  # type: ignore[arg-type]
        return out

    problems = []
    for rel, want in expected.items():
        declared = _declared(root / rel)
        assert declared, f"{rel}: no sdk.tortoise_fts_query call site found"
        for got in declared:
            if got != want:
                problems.append(f"{rel}: _valid_time_rerank={got!r}, want {want!r}")
    assert not problems, "\n".join(problems)


def test_the_capture_prior_leg_opts_out_of_the_selection():
    """FAIL VALUE: S3's capture-time prior leg inheriting the read-path
    valid-time selection (#8487 review round 7, P2).

    ``tortoise/extractor_v2.py::_fts_rows`` is the same shape as
    ``_review_pool`` / ``_resolve_subgraph_seed``: ``search_graph`` accumulates
    its rows into id-keyed dicts (order discarded) and link-before-create /
    supersession resolution consumes them as a SET — at ``limit=3``, and on the
    one path that PERSISTS the resulting prior. Leaving the selection on could
    only move WHICH ids survive that 3-wide cut.

    Reachable: ``_fts_rows`` is called directly with a recording stub, so the
    kwargs asserted are the exact ones a live call sends. Pre-fix the kwarg is
    absent (``kwargs.get(...) is None``), so this test fails on pre-fix code.
    """
    from tortoise import extractor_v2

    class _Recorder:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []

        def tortoise_fts_query(self, query, **kwargs):
            self.calls.append((query, kwargs))
            return []

    sdk = _Recorder()
    for entity_type in ("point", "object", "subject", "event"):
        extractor_v2._fts_rows(sdk, entity_type, "billing ledger now")
    assert len(sdk.calls) == 4, sdk.calls
    problems = [
        f"entity_type={entity_type}: _valid_time_rerank="
        f"{kwargs.get('_valid_time_rerank')!r} (kwargs: {sorted(kwargs)})"
        for (_q, kwargs), entity_type in zip(sdk.calls,
                                             ("point", "object", "subject", "event"),
                                             strict=True)
        if kwargs.get("_valid_time_rerank") is not False
    ]
    assert not problems, (
        "the capture prior leg re-owns its order and must opt out of the "
        "valid-time selection:\n" + "\n".join(problems))
