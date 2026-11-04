"""#2517 (C4, #2513) — source-session re-injection: arm wiring + E2E.

The reader-surface + pool-rank-cut lever. Product rules live in
``tortoise/session_reinjection.py``; ``tools/longmem_eval/retrieve.py``
composes SEED → EXPAND → MERGE on the annotated pool, inserted AFTER the
C3-1 block and BEFORE the C2 boost.

Hermetic contract proven here (docker lane, dense leg pinned out):

  * (a) the END-TO-END flip: a seeded session's injected items trigger the
        shared guard, which admits a pool-present starved session into
        ``hits[:5]`` (``session_recall@5`` 0.5 → 1.0); with the guard
        ablated OFF the flip does NOT happen — the flip is attributable to
        the guard, not the fetched items (falsifier 1),
  * (b) default OFF == explicit OFF, byte-identical ranked ids,
  * (c) fail-open: a fetch failure, a zero-new-ids fetch, AND a forced
        merge-stage exception each return the base pool unchanged; a census
        (measurement) failure leaves the merged pool INTACT and records
        ``measured: False`` — a measurement never decides the arm,
  * (d) caps: the C5 per-session raw-chunk cap holds on the re-injected
        pool. ⚠️ The C5 re-cap counts only raw chunks, so this test pins
        the arm at the NON-default chunk grain (``_pin_chunk_grain``); the
        shipped turn grain is deliberately not re-capped (the total budget
        is its volume guard),
  * (e) TR questions are excluded,
  * (f) the guard is a no-op on a single-session pool,
  * (g) guard OFF still re-caps through the same shared contract (also on
        the pinned chunk grain),
  * (h) the census keys, the resolved-arm env gate, the CLI flags, and the
        both-arms-ON ABORT (message + exit). The fingerprint-refusal and
        arm-conflict gates are NOT here: they are hermetic run-level gates
        and live in ``tests/test_session_reinjection_rules.py``, because
        this module skips as a whole on an unavailable FalkorDB probe,
  * (i) the PRODUCT turn shape: a turn Point written the way
        ``capture_session`` writes it (no ``session_id`` property, linked
        ``Session-[:CONTAINS]->Point``) is injected and grouped under its
        ``:Session`` id, and a non-turn ``pointKind='event'`` point (a LEGACY
        demo-seed node, which the seeder has not written since #10000) is NOT —
        the fixture's ``CONTAINS``
        membership is per session, so a cross-session leak is visible here.
  * (j) the READER-WINDOW census counts the surface the arm actually
        delivers: on the pool-only isolation arm (``--rerank off
        --rerank-pool N``) that surface is the ``top_k`` prefix, so the
        census window is ``top_k`` there and the resolved item cap (40) on
        the default arm.

Requires the docker lane's FTS backend — skips when unavailable.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

# import the eval ingest first so its register_kind("event") /
# register_kind("session-transcript") apply.
import tools.longmem_eval.ingest  # noqa: F401
from tests import _live_utils
from tools.longmem_eval.retrieve import EVAL_RETRIEVAL_BUDGET_MS
from tortoise.retrieval import (
    DEFAULT_POOL_SESSION_CAP,
    SESSION_TRANSCRIPT_KIND,
)
from tortoise.sdk import TortoiseSDK

MINI = Path(__file__).resolve().parent / "fixtures" / "longmemeval_mini.json"


# ── Live-FalkorDB availability (the FTS backend the arm needs) ───────────
def _falkordb_available() -> bool:
    """Probe a live FalkorDB; reads TORTOISE_DB_URI at CALL time so the
    module never captures it at import (#221 test-isolation lint)."""
    uri = os.environ.get(
        "TORTOISE_DB_URI",  # empty-uri-default-ok: #2815 skip declared; converting flips the tier-2 lane
        _live_utils.docker_uri("tortoise_test_matrix")).rstrip("/")
    old = os.environ.get("TORTOISE_DB_URI")
    try:
        os.environ["TORTOISE_DB_URI"] = f"{uri}_probe"
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


def _uri() -> str:
    """Current TORTOISE_DB_URI (or the default), read at CALL time."""
    return os.environ.get(
        "TORTOISE_DB_URI",  # empty-uri-default-ok: #2815 skip declared; converting flips the tier-2 lane
        _live_utils.docker_uri("tortoise_test_matrix")).rstrip("/")


pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE,
    reason="requires TORTOISE_DB_URI (live FalkorDB FTS lane — embedded "
           "legs skip)")


QUESTION = "how much did the road bike repairs cost me in total"
QID = "q2517test"
SR_A = "sessA2517"        # the seeded, monopolising session
SR_B = "sessB2517"        # the pool-present starved session
MATCH_CONTENT = "the road bike repairs cost me 120 dollars in total"
#: #2517: fabricated turns carry the PRODUCT turn shape — every product turn
#: writer stores ``f"[{role}] {content}"`` with ``is_episodic=true``
#: (``sdk.capture_session``, hosted ``POST /v1/sessions``), and the C4 fetch
#: constrains ``pointKind='event'`` to exactly that shape (the kind
#: vocabulary is open, so the kind alone does not prove a turn).
TURN_PREFIX = "[user] "
#: the chunks carry NO query token, so plain FTS never puts them in the
#: pool — they are genuinely-new material for the C4 fetch.
CHUNK_CONTENT = ("in the afternoon we watched a documentary about "
                 "volcanoes and then went to bed early")
#: the session's OTHER turns (also off-pool) — the raw material the fetch
#: injects at the product's default TURN grain.
OFF_POOL_TURN_CONTENT = ("we also talked about the weather and about "
                         "which documentary to watch next week")


def _fresh_uri() -> str:
    """A dedicated per-test graph on the docker server — fresh indexes and
    an empty graph make the differential hermetic."""
    return f"{_uri()}_{uuid.uuid4().hex[:10]}"


# ── the retrieval deadline: pinned, never inherited ──────────────────────
#
# ⛔ EVERY differential in this module compares TWO reads' hits. That is a
# statement about the ARM only while both reads were served from the same
# retrieval legs — and the SDK's product default (a 500 ms collective
# deadline, #1786 R5) is ALSO the per-query ``TIMEOUT`` handed to FalkorDB
# (`search_engine.py:1724` — the `run_fts_query` leg's
# `graph.query(..., timeout=timeout_ms)`; `:1730` is the post-hoc warning it
# logs six lines below, which is where an earlier version of this note pointed
# a reader who asked for the handoff). When BOTH sparse strategies miss it the engine
# collects 0/2, the SDK degrades to the corpus-wide ``tfidf_snapshot``
# fallback, and that pool is a DIFFERENT COMPOSITION — measured on this
# fixture's graph: 11 of 11 seeded Points, id-ordered, against the 7 the FTS
# leg returns. So one read degrading while the other does not turns a loaded
# box into a false RED: CI run 41741210621, job ``test (i)``, did exactly
# that at 500 ms ("FTS query exceeded timeout: 1220ms > 500ms", "Strategies
# timed out (500ms) — collected 0/2") and failed
# ``test_reinjection_disabled_is_default_byte_identical`` with
# ``srAc0``/``srAc1``/``srAq0``/``srAq1`` as the four extra items.
#
# Pinned to the EVAL's own budget rather than a larger number: this call path
# is the eval's (``run.py`` passes ``EVAL_RETRIEVAL_BUDGET_MS``), and the
# value must stay under the server's ``TIMEOUT_MAX``. The docker lane starts
# FalkorDB with ``FALKORDB_ARGS=THREAD_COUNT 8 TIMEOUT_MAX 30000``
# (tools/test_lane.py), and a per-query TIMEOUT above it does not degrade
# gracefully — the LEG FAILS (measured here: 60000 → fts ``query_failed`` +
# structural ``query_failed`` → the same corpus fallback, 11 hits).


def _leg_mix(ret: dict) -> list[tuple[str, int]]:
    """The legs that actually CONTRIBUTED hits to a read: ``(leg, count)``.

    Two reads are comparable hit-for-hit only when these agree. A leg that
    ran and returned ZERO cannot move the pool, so ``timeout`` vs
    ``empty_results`` is deliberately NOT a difference here; a leg that
    contributed hits is, and so is the corpus-wide ``fallback`` (its presence
    at count>0 is the whole point of the guard).
    """
    return sorted((leg["leg"], leg["count"])
                  for leg in ret.get("legs") or [] if leg.get("count"))


#: Leg reasons that mark a read DEGRADED. The engine's own ``degraded`` flag is
#: the authority (``search_engine._trace_entry``: "``reason`` is never null
#: when ``degraded`` is true") and is what this module reads second; the set is
#: a belt for a trace assembled outside the engine (a fixture, or a producer
#: that predates the flag) so such a read can never masquerade as healthy.
#: ``no_embedder``/``no_query_vector``/``empty_results``/``below_relevance_floor``
#: are deliberately ABSENT: this module pins the dense leg out on purpose, and
#: a leg that ran and found nothing did not degrade.
_DEGRADED_REASONS = frozenset({
    "timeout", "query_failed", "breaker_open", "index_missing",
    "no_embeddings", "tfidf_snapshot", "tfidf_fallback",
})

#: Reasons that mean a leg was DELIBERATELY not run, which this module must
#: read BEFORE the engine's ``degraded`` flag (#10675 review, P1).
#:
#: ⛔ The flag alone is not enough, and that is the whole trap: the engine marks
#: a designed absence ``degraded=True`` at the source — ``sdk.py``'s status_trace
#: append for the vector leg is ``ran=False, degraded=True, reason=no_embedder``
#: — so a guard that reads ``degraded`` FIRST finds every read in this module
#: degraded (the ``_no_embedder`` fixture below is autouse), ``any(degraded)``
#: is then always true, and ``_require_one_leg_mix``'s FAIL branch can never
#: run. That is an inert enforcer: a real, code-caused leg divergence would
#: SKIP — non-red to the rail — which is the exact fail-open the FAIL branch
#: exists to close. The absence therefore has to be excluded by REASON before
#: the flag is consulted.
_DESIGNED_ABSENCE_REASONS = frozenset({"no_embedder", "no_query_vector"})


def _degradations(ret: dict) -> list[tuple[str, str]]:
    """The legs of a read that DEGRADED, as ``(leg, reason)`` pairs.

    Reads the RECORD (``degraded`` / a results-bearing ``fallback``), never the
    difference between two reads: a difference is what this module is trying to
    measure, so it cannot also be the evidence that the measurement is void.
    """
    out = []
    for leg in ret.get("legs") or []:
        name = str(leg.get("leg"))
        reason = str(leg.get("reason") or "")
        # A leg that was never supposed to run did not degrade — read this
        # BEFORE the flag, because the engine sets ``degraded=True`` for it.
        if reason in _DESIGNED_ABSENCE_REASONS:
            continue
        # ⛔ THE EMPTY FALLBACK IS THE SAME TRAP ONE LEG OVER (#10675 review
        # cycle 4, P1): ``sdk.py`` appends the fallback as ``ran=True,
        # degraded=True, reason='no_fallback_applicable', count=0``, i.e. the
        # engine flags a leg that RAN AND FOUND NOTHING as degraded. Counting it
        # here made ``_degradations`` non-empty on the armed half of a comparison
        # (2 of 39 armed reads in one ordinary run), so a real, code-caused
        # leg-mix divergence SKIPPED — non-red to the rail — which is the fail-open
        # ``_DESIGNED_ABSENCE_REASONS`` was added to close. The engine's own
        # contract says so: ``search_engine._results_bearing_fallback`` — “count
        # == 0 (no_fallback_applicable) returns no rows and is not
        # disqualifying”. Read the COUNT, not the flag, exactly as the reason set
        # is read before it.
        if name == "fallback" and not (leg.get("count") or 0):
            continue
        if (leg.get("degraded")
                or (name == "fallback" and (leg.get("count") or 0) > 0)
                or reason in _DEGRADED_REASONS):
            out.append((name, reason))
    return sorted(out)


def _require_one_leg_mix(*rets, test: str) -> None:
    """Refuse to compare reads the box served from DIFFERENT legs.

    Two outcomes, and the difference between them is the DEGRADATION, not the
    difference (#10675 review, P3). ⚠️ Both outcomes are reachable ONLY when
    the mixes DISAGREE — the early return below fires first, so a pair that
    degraded IDENTICALLY is compared and publishes a verdict rather than
    skipping (#10675 review cycle 5, P3: this docstring claimed "a degraded
    read → skip" without that qualifier, so the next reader would have believed
    the identical-degraded case was skipped and never re-derived it. It is
    stated, not changed: a skip there would put the load-dependent red straight
    back, and an identical mix is by construction comparable).

    * **A degraded read AND disagreeing mixes → skip.** One read lost a leg to
      the environment (the measured CI red: run 41741210621, ``test (i)``, an
      FTS ``timeout`` at 1220 ms > 500 ms, so that read answered from the
      corpus-wide ``tfidf_snapshot`` pool). The arm differential is
      unmeasurable in that state, and comparing anyway is what produced the
      load-dependent RED this module pins out. Skipped with BOTH mixes AND both
      degradation lists named (never silently).
    * **NO degraded read → FAIL.** Both reads were served cleanly and their
      leg mixes still disagree, so the divergence is the ARMS' doing — a code
      change (e.g. one arm reaching ``hybrid_search`` with a different
      ``entity_types`` set) — and the module's central assertion
      (``test_reinjection_disabled_is_default_byte_identical``) would publish
      no verdict at all if this skipped. Skipping there is the fail-open arm
      the review named, and it is exactly the class of hole a predicate over
      "difference" cannot see.
    """
    mixes = [_leg_mix(r) for r in rets]
    if not any(m != mixes[0] for m in mixes[1:]):
        return
    degraded = [_degradations(r) for r in rets]
    if any(degraded):
        pytest.skip(
            f"{test}: the box served these reads from different retrieval "
            f"legs, and at least one read DEGRADED, so a hit-for-hit "
            f"comparison would compare two different pools — measured "
            f"mixes: {mixes}; degraded legs (leg, reason): {degraded}")
    pytest.fail(
        f"{test}: the two reads were served from DIFFERENT retrieval legs "
        f"with NO leg degraded on either side, so this is the ARMS behaving "
        f"differently, not the box — measured mixes: {mixes}; degraded legs: "
        f"{degraded}. Fix the divergence instead of skipping it: a skip here "
        f"would publish no verdict for a code-caused confound "
        f"(#10675 review, P3)", pytrace=False)


@pytest.fixture(autouse=True)
def _pinned_retrieval_budget(monkeypatch):
    """Force the eval's collective deadline onto every read in this module.

    Wraps ``hybrid_search`` — the ONE seam every ``retrieve_for_question``
    call reaches — rather than passing ``retrieval_budget_ms`` at each call
    site: a per-site pin is precisely what was missing (every call site
    inherited the 500 ms product default), so a call site must not be able to
    forget it. The value is FORCED, not defaulted: an explicit ``None`` from
    the caller resolves to the SDK default, which is the deadline that made
    this module load-dependent.
    """
    from tools.longmem_eval import retrieve as _retrieve

    def _forced(*args, **kwargs):
        kwargs["retrieval_budget_ms"] = EVAL_RETRIEVAL_BUDGET_MS
        return _real(*args, **kwargs)

    _real = _retrieve.hybrid_search
    monkeypatch.setattr(_retrieve, "hybrid_search", _forced)


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the dense leg OUT (hermetic): the differential isolates the
    sparse pool mechanics. No model load, no network.

    Also pin the ARM ENV to unset: the OFF-arm tests assert byte-identity
    against the documented default, so an ambient
    ``TORTOISE_LME_SESSION_REINJECTION`` / ``TORTOISE_LME_CONTEXT_ITEMS``
    (which resizes the derived seed window) would silently flip the arm
    under test.

    #2513 (#10629 review P3): the same hazard applies to the THREE ambient
    twins the resolved-arm assertions read, so they are pinned here rather
    than one test at a time — a measurement lane that arms the A/B through
    the documented env twins would otherwise get a FALSE RED in the very
    test that pins the arm:

    * ``TORTOISE_LME_GUARD_PER_SESSION_CAP`` — ``_resolve_guard_per_session_cap``
      reads it, so an exported value reds the ``guard_per_session_cap ==
      DEFAULT_POOL_SESSION_CAP`` assertion of the ``--no-floor-term`` case.
    * ``TORTOISE_LME_COVERAGE_LOOP`` — it turns a pool-order arm on, so an
      exported value makes the ``--floor-term``-alone run legal instead of
      the ``ArmConflictError`` (exit 1) that case asserts.
    * ``TORTOISE_LME_FLOOR_TERM`` — an exported value silently arms the
      lower bound under every OFF-arm assertion."""
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get",
                        staticmethod(lambda: None))
    monkeypatch.delenv("TORTOISE_LME_SESSION_REINJECTION", raising=False)
    monkeypatch.delenv("TORTOISE_LME_CONTEXT_ITEMS", raising=False)
    # #2513 (#10629 review P3): hermeticity for the resolved-arm assertions.
    monkeypatch.delenv("TORTOISE_LME_FLOOR_TERM", raising=False)
    monkeypatch.delenv("TORTOISE_LME_GUARD_PER_SESSION_CAP", raising=False)
    monkeypatch.delenv("TORTOISE_LME_COVERAGE_LOOP", raising=False)


def _question(question: str = QUESTION, *,
              question_type: str = "multi-session-reasoning",
              answer_sessions: list[str] | None = None) -> dict:
    return {
        "question_id": QID,
        "question": question,
        "question_type": question_type,
        "answer_session_ids": (answer_sessions
                               if answer_sessions is not None
                               else [SR_A, SR_B]),
        "haystack_session_ids": [SR_A, SR_B],
        "haystack_dates": ["2026-09-01", "2026-09-05"],
        "haystack_sessions": [],
        "answer": "",
    }


def _seed_graph(sdk, *, n_a: int = 6, n_turns: int = 2,
                n_chunks: int = 2) -> None:
    """A fresh dedicated graph in the PRODUCT's shape: ``:Session`` nodes
    with PER-SESSION ``CONTAINS`` edges (the membership the C4 fetch scopes
    on — a product turn Point carries no ``session_id`` property), episodic
    turn Points whose body is ``[role] …``, and raw chunks."""
    for sid in (SR_A, SR_B):
        sdk._get_proj().g.query(
            "MERGE (s:Session {id:$sid}) SET s.is_episodic=true",
            params={"sid": sid})
    for i in range(n_a):
        sdk.create_point("event", TURN_PREFIX + MATCH_CONTENT,
                         id=f"srAp{i}", session_id=SR_A, speaker="user",
                         is_episodic=True, status="draft")
    sdk.create_point("event", TURN_PREFIX + MATCH_CONTENT, id="srBp0",
                     session_id=SR_B, speaker="user",
                     is_episodic=True, status="draft")
    # the session's OWN other turns — off-pool, so the fetch has
    # genuinely-new TURN material to inject at the product's default grain
    for i in range(n_turns):
        sdk.create_point("event", TURN_PREFIX + OFF_POOL_TURN_CONTENT,
                         id=f"srAq{i}", session_id=SR_A, speaker="user",
                         is_episodic=True, status="draft")
    for i in range(n_chunks):
        sdk.create_point("session-transcript", CHUNK_CONTENT,
                         id=f"srAc{i}", session_id=SR_A, status="draft")
    _link_sessions(sdk)
    # POST-CONDITION: membership is PER SESSION (exactly one CONTAINS edge
    # per seeded point). The fixture used to write the CARTESIAN product
    # (``p.session_id IN $sids`` with no join), which linked every Session
    # to every Point — so a leak of a DIFFERENT session's points was
    # invisible to this lane, which is the failure the retarget prevents.
    n_edges = sdk._get_proj().g.query(
        "MATCH (:Session)-[c:CONTAINS]->(:Point) RETURN count(c)"
    ).result_set[0][0]
    expected = n_a + 1 + n_turns + n_chunks
    assert n_edges == expected, (
        f"per-session CONTAINS membership expected {expected}, "
        f"got {n_edges} — the fixture is linking across sessions")


def _link_sessions(sdk, extra: dict[str, list[str]] | None = None) -> None:
    """Wire ``Session-[:CONTAINS]->Point`` PER SESSION (the eval ingest
    and the product capture loop both write it; the C4 fetch resolves the
    session through it).

    ``extra`` links points that carry NO ``session_id`` (the product turn
    loop's property set) to their session EXPLICITLY — the branch the
    fetch's ``coalesce(p.session_id, s.id)`` group key exists for.
    """
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (s:Session), (p:Point) WHERE s.id IN $sids "
        "  AND p.session_id = s.id "
        "MERGE (s)-[:CONTAINS]->(p)",
        params={"sids": [SR_A, SR_B]})
    for sid, pids in (extra or {}).items():
        for pid in pids:
            proj.g.query(
                "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
                "MERGE (s)-[:CONTAINS]->(p)",
                params={"sid": sid, "pid": pid})


def _create_product_turn(sdk, pid: str, content: str) -> None:
    """Write a turn the way ``capture_session`` does: NO ``session_id``
    property, ``pointKind='event'``, ``is_episodic=true``, role-prefixed
    body. The fetch must still group it under its ``:Session`` id."""
    sdk._get_proj().g.query(
        "MERGE (t:Point {id:$id}) "
        "SET t.content=$c, t.pointKind='event', t.is_operator=false, "
        "    t.speaker='user', t.is_episodic=true, t.status='draft'",
        params={"id": pid, "c": content})


def _create_demo_event(sdk, pid: str, content: str) -> None:
    """A LEGACY demo-seed shape: ``pointKind='event'`` with NO
    ``is_episodic``. Its body IS role-tagged, so ``is_episodic`` is the
    conjunct that excludes it — not the ``[``-prefix test.

    ⛔ This synthesizes a node a graph seeded BEFORE #10000 still holds. Since
    #10000 the hosted demo/dashboard seed writes no turn node at all (§9.1/D1:
    the transcript is Postgres'), so this helper describes no current writer —
    it exists to pin that a legacy graph's nodes stay excluded."""
    sdk._get_proj().g.query(
        "MERGE (t:Point {id:$id}) "
        "SET t.content=$c, t.pointKind='event', t.is_operator=false, "
        "    t.status='live'",
        params={"id": pid, "c": content})


@pytest.fixture
def seeded_sdk(monkeypatch):
    """A fresh dedicated graph: session A holds SIX identical-content
    matching points (so it monopolises the top of the pool), session B holds
    ONE (same content → tied score, id tiebreak puts A's ids first, so B
    lands at rank 6), and session A owns raw chunks absent from the pool."""
    monkeypatch.setenv("TORTOISE_DB_URI", _fresh_uri())
    sdk = TortoiseSDK()
    try:
        _seed_graph(sdk)
        yield sdk
    finally:
        sdk.close()


# ── (a) the end-to-end flip, attributable to the guard ──────────────────

def test_reinjection_flips_a_starved_session_into_top5(seeded_sdk):
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question()
    off = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60)
    off_ids = [h["id"] for h in off["hits"]]
    assert "srAp0" in off_ids[:5]
    assert "srBp0" not in off_ids[:5], (
        "the monopolised pool must starve session B out of the top-5")
    assert off["session_recall@k"]["5"] == 0.5
    # off-arm census is the zeroed shape (reconstructable per question) — and
    # ``measured: False``, because NO census runs on this arm
    assert off["session_reinjection"] is False
    st_off = off["session_reinjection_stats"]
    assert st_off["reader_window_displacement"]["measured"] is False
    assert st_off["seeded"] == 0
    assert st_off["injected_total"] == 0
    assert st_off["injected_merged"] == 0
    assert st_off["fetch_ok"] is False
    assert st_off["guard"] is True

    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60, session_reinjection=True)
    on_ids = [h["id"] for h in on["hits"]]
    assert on["session_reinjection"] is True
    st = on["session_reinjection_stats"]
    assert st["on"] is True
    # B is pool-present at rank 6, inside the reader-reachable seed
    # window (40) — seeded too, but it owns no off-pool turns to fetch
    assert st["seed_sessions"] == [SR_A, SR_B]
    assert st["seeded"] == 2
    assert st["fetch_ok"] is True
    assert st["injected_total"] == 2
    assert st["injected_per_session"] == {SR_A: 2}
    assert st["injected_merged"] >= 1
    assert "srBp0" in on_ids[:5], (
        "the guard must admit the pool-present starved session into the "
        "official top-5 window")
    assert on["session_recall@k"]["5"] == 1.0
    # the seed is never displaced
    assert "srAp0" in on_ids[:5]
    # the injected ids are the session's off-pool TURNS — the product's
    # default grain — NOT its raw chunks. This is the assertion that makes
    # the flip test discriminate the grain: at ``session-transcript`` the
    # injected set would be {srAc0, srAc1} and these two lines would fail.
    assert {"srAq0", "srAq1"} <= set(on_ids)
    assert not {"srAc0", "srAc1"} & set(on_ids)
    # the injected items carry the driver's OWN leg
    assert any(h["match_source"] == "session" for h in on["hits"])


def test_flip_is_attributable_to_the_guard_not_the_fetch(seeded_sdk):
    """Falsifier 1: with the guard ablated OFF, the injected items still
    merge but the starved session does NOT flip into the top-5 — so the
    flip above is the guard's, not the fetch's."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question()
    abl = retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
        session_reinjection=True, session_reinjection_guard=False)
    st = abl["session_reinjection_stats"]
    assert st["guard"] is False
    assert st["injected_total"] == 2
    assert "srBp0" not in [h["id"] for h in abl["hits"][:5]]
    assert abl["session_recall@k"]["5"] == 0.5


# ── (b) default OFF is byte-identical ────────────────────────────────────

def test_reinjection_disabled_is_default_byte_identical(seeded_sdk):
    import inspect

    from tools.longmem_eval.retrieve import retrieve_for_question
    sig = inspect.signature(retrieve_for_question)
    assert sig.parameters["session_reinjection"].default is None
    q = _question()
    default = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                    pool_size=60)
    off = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60, session_reinjection=False)
    _require_one_leg_mix(default, off, test="default-OFF == explicit-OFF")
    assert [h["id"] for h in default["hits"]] == [h["id"] for h in off["hits"]]
    assert default["session_reinjection"] is False
    assert default["session_reinjection_stats"] == \
        off["session_reinjection_stats"]


def test_the_differential_pins_the_eval_retrieval_budget(seeded_sdk,
                                                         monkeypatch):
    """Every read here carries the eval's deadline, not the SDK's 500 ms.

    FAILS IF: ``_pinned_retrieval_budget`` is removed — the captured value is
    then ``None`` (the SDK's product default), and that is the deadline under
    which CI's loaded runner degraded ONE of two compared reads (fts>
    500 ms, 0/2 collected) into the corpus-wide fallback and turned the arm
    differential into a false RED.

    ⛔ The budget's HEADROOM is a SEPARATE, BY-VALUE pin at the end of this
    test — not a claim in this docstring. Comparing ``EVAL_RETRIEVAL_BUDGET_MS``
    with a bound that is itself written in terms of
    ``EVAL_RETRIEVAL_BUDGET_MS`` moves with it and asserts nothing; the earlier
    revision of this docstring claimed a ``TIMEOUT_MAX`` falsifier that no
    assertion here could have had (#10675 review P3).
    """
    from tools.longmem_eval.retrieve import retrieve_for_question

    captured: list = []

    def _capture(self, query, entity_type="point", limit=10, **kw):
        captured.append(kw.get("_elevated_timeout_ms"))
        return []

    monkeypatch.setattr(TortoiseSDK, "tortoise_fts_query", _capture)
    retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                          pool_size=60)
    assert captured, "the read reached no tortoise_fts_query call at all"
    assert captured == [EVAL_RETRIEVAL_BUDGET_MS] * len(captured), (
        "the reads in this module must carry the eval's collective deadline; "
        f"got {captured} against {EVAL_RETRIEVAL_BUDGET_MS}")
    # ⛔ A LITERAL ceiling, deliberately not a comparison against the constant
    # itself — that is the only shape here that can red on the pin being raised
    # too far, and it is what makes the headroom half non-vacuous. The lane's
    # ``TIMEOUT_MAX`` does NOT degrade above the cap: the server REFUSES the
    # query, so the leg ends in ``query_failed`` and lands on the same
    # corpus-wide fallback the fixture above exists to keep out of the
    # comparison (measured: 60000 -> fts + structural ``query_failed``).
    assert EVAL_RETRIEVAL_BUDGET_MS < 30000, (
        "the eval's retrieval deadline must stay under the private lane's "
        "`TIMEOUT_MAX`, else a read is refused instead of degraded and the "
        "arm comparison measures the refusal. Ceiling source: "
        "tools/test_lane.py::LANE_FALKORDB_ARGS = "
        "'THREAD_COUNT 8 TIMEOUT_MAX 30000' (the lane this module runs in). "
        f"Got EVAL_RETRIEVAL_BUDGET_MS={EVAL_RETRIEVAL_BUDGET_MS}")


def test_a_pair_served_by_different_legs_is_not_compared():
    """``_require_one_leg_mix`` refuses the unmeasurable comparison.

    FAILS IF: the guard degrades to a no-op (the load-dependent RED returns
    as an id diff nobody can attribute) or starts refusing pairs that ARE
    comparable.
    """
    # ⛔ ``degraded: True`` on the dense leg is the shape the ENGINE emits for a
    # designed absence (``sdk.py``'s status_trace append), not a nicety: without
    # it these fixtures describe a trace no producer creates, and the guard's
    # real-trace behaviour goes untested (#10675 review, P1).
    healthy = {"legs": [{"leg": "vector", "ran": False, "degraded": True,
                          "reason": "no_embedder", "count": 0},
                         {"leg": "fts", "ran": True,
                          "reason": "ok", "count": 7}]}
    degraded = {"legs": [{"leg": "vector", "ran": False, "degraded": True,
                          "reason": "no_embedder", "count": 0},
                         {"leg": "fts", "ran": True,
                          "reason": "query_failed", "count": 0},
                         {"leg": "fallback", "ran": True,
                          "reason": "tfidf_snapshot", "count": 11}]}
    # comparable: same mix (a second read of the same healthy shape)
    _require_one_leg_mix(healthy, {"legs": list(healthy["legs"])},
                         test="probe")
    with pytest.raises(pytest.skip.Exception) as exc:
        _require_one_leg_mix(healthy, degraded, test="probe")
    assert "different retrieval legs" in str(exc.value)
    # the mixes are NAMED, so the log says which leg moved
    assert "'fallback'" in str(exc.value) and "('fts', 7)" in str(exc.value)


def test_a_zero_count_leg_difference_is_not_a_confound():
    """A leg that ran and returned ZERO cannot move the pool.

    ``timeout`` vs ``empty_results`` on a leg that contributed nothing is a
    scheduling difference, not a pool difference — refusing to compare there
    would throw away measurable runs on any loaded box, which is the opposite
    failure to the one the guard fixes.

    FAILS IF: the guard reverts to comparing ``(leg, ran, reason)`` — it then
    skips this comparable pair.
    """
    empty_timeout = {"legs": [{"leg": "fts", "ran": True,
                               "reason": "ok", "count": 7},
                              {"leg": "structural", "ran": True,
                               "reason": "timeout", "count": 0}]}
    empty_none = {"legs": [{"leg": "fts", "ran": True,
                            "reason": "ok", "count": 7},
                           {"leg": "structural", "ran": True,
                            "reason": "empty_results", "count": 0}]}
    _require_one_leg_mix(empty_timeout, empty_none, test="probe")


def test_two_HEALTHY_reads_on_different_legs_FAIL_rather_than_skip():
    """A difference with NO degradation is the ARMS, and must go RED.

    ``_require_one_leg_mix`` used to skip on ANY difference, which is a
    predicate over the difference rather than over the degradation: a code
    change that made one arm reach a different retrieval leg would have been
    "unmeasurable" forever, and the module's central arm differential would
    publish no verdict at all (#10675 review, P3).

    FAILS ON the pre-fix guard: it called ``pytest.skip`` here instead.

    ⛔ Why this does NOT use ``pytest.raises(pytest.fail.Exception)``: a
    ``pytest.skip`` raises ``Skipped``, which that context manager does not
    suppress, so the pre-fix guard would report as a SKIP — non-red for the
    merge rail — and this pin would be fail-OPEN on the exact regression it
    names. Measured (#10675 review, P2): the pre-fix guard skipped here, and so
    did the realistic half-fix (restore the skip, keep the improved message),
    with all three of these tests green. Catching ``BaseException`` is what
    makes a FAIL and a SKIP distinguishable, so the skip arm can be asserted
    against rather than silently satisfying the test.
    """
    fts_only = {"legs": [{"leg": "fts", "ran": True, "degraded": False,
                           "reason": "ok", "count": 7}]}
    fts_and_structural = {"legs": [
        {"leg": "fts", "ran": True, "degraded": False,
         "reason": "ok", "count": 7},
        {"leg": "structural", "ran": True, "degraded": False,
         "reason": "ok", "count": 3}]}
    try:
        _require_one_leg_mix(fts_only, fts_and_structural, test="probe")
    # BaseException on purpose: FAIL vs SKIP IS the assertion (see docstring).
    except BaseException as exc:
        assert not isinstance(exc, pytest.skip.Exception), (
            "the guard SKIPPED two HEALTHY reads served from different legs, "
            "so a code-caused divergence still publishes no verdict for the "
            "module's central arm differential (#10675 review, P3); a skip "
            f"is non-red to the rail. Got: {exc}")
        assert isinstance(exc, pytest.fail.Exception), (
            "expected a FAIL for a leg divergence with no degradation on "
            f"either side, got {type(exc).__name__}: {exc}")
        message = str(exc)
    else:
        raise AssertionError(
            "the guard ACCEPTED two healthy reads served from different "
            "legs: a difference with no degradation is the ARMS, not the "
            "box, and must go RED rather than pass (#10675 review, P3)")
    # names the divergence AND that nothing degraded — the two facts the next
    # reader needs to tell "fix the arms" from "wait for a quieter box".
    assert "('structural', 3)" in message and "degraded legs: [[]" in message
    assert "probe" in message


def test_BOTH_reads_degraded_is_still_not_compared():
    """Two degraded reads stay a SKIP: the environment, not the arms.

    A loaded box can lose a DIFFERENT leg on each read (fts on one, structural
    on the other). That is unmeasurable for the same reason one degraded read
    is, and failing it would put the load-dependent RED straight back.

    FAILS IF: the degradation test becomes "exactly one arm degraded" — this
    pair then reddens a box that simply could not answer the question.
    """
    fts_degraded = {"legs": [
        {"leg": "fts", "ran": True, "degraded": True,
         "reason": "timeout", "count": 0},
        {"leg": "fallback", "ran": True, "degraded": True,
         "reason": "tfidf_snapshot", "count": 11}]}
    structural_degraded = {"legs": [
        {"leg": "fts", "ran": True, "degraded": False,
         "reason": "ok", "count": 7},
        {"leg": "structural", "ran": True, "degraded": True,
         "reason": "query_failed", "count": 0}]}
    with pytest.raises(pytest.skip.Exception) as exc:
        _require_one_leg_mix(fts_degraded, structural_degraded, test="probe")
    message = str(exc.value)
    assert "different retrieval legs" in message
    assert "'timeout'" in message and "'query_failed'" in message


def test_a_degraded_leg_that_returned_rows_is_a_degradation():
    """A results-bearing ``fallback`` is a degradation even flagless.

    The trace contract sets ``degraded`` on a ``fallback`` entry, but a trace
    assembled outside the engine (a fixture, or a producer predating the flag)
    can omit it. ``fallback`` at count>0 is definitive on its own: the pool is
    the corpus-wide TF-IDF snapshot, a DIFFERENT composition, not a leg mix.

    FAILS IF: ``_degradations`` reads only the ``degraded`` key — the pair below
    then fails as a code-caused divergence.
    """
    assert _degradations({"legs": [
        {"leg": "fallback", "ran": True, "reason": "tfidf_snapshot",
         "count": 11}]}) == [("fallback", "tfidf_snapshot")]
    # ...while a fallback that produced NOTHING is not one (nothing moved) —
    # and the fixture carries the shape ``sdk.py`` ACTUALLY appends, ``degraded:
    # True`` included. Omitting that key (as this arm first did) asserted a trace
    # no producer creates, which is why the real fail-open — a diverged armed read
    # SKIPPING because of this entry — went unnoticed (#10675 review cycle 4, P1).
    assert _degradations({"legs": [
        {"leg": "fallback", "ran": True, "degraded": True,
         "reason": "no_fallback_applicable", "count": 0}]}) == []
    # ...and the deliberate dense-leg omission is never a degradation, even
    # though the engine flags it ``degraded=True`` (the shape asserted here is
    # the one ``sdk.py`` actually appends — see _DESIGNED_ABSENCE_REASONS).
    assert _degradations({"legs": [
        {"leg": "vector", "ran": False, "degraded": True,
         "reason": "no_embedder", "count": 0}]}) == []


# ── (c) fail-open: fetch failure / zero new ids / merge exception ────────

def _base_pool(sdk, q):
    """The arm-OFF read as a whole result — the fail-open tests below need
    its ``legs`` as well as its ids (``_require_one_leg_mix``)."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    return retrieve_for_question(sdk, q, ks=(5,), top_k=10, pool_size=60)


def test_fetch_failure_keeps_the_base_pool(seeded_sdk, monkeypatch):
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise import session_reinjection as _sr
    q = _question()
    base_ret = _base_pool(seeded_sdk, q)
    base = [h["id"] for h in base_ret["hits"]]

    def _boom(*a, **kw):
        raise RuntimeError("fetch blew up")

    monkeypatch.setattr(_sr, "source_session_chunk_pass", _boom)
    ret = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60, session_reinjection=True)
    _require_one_leg_mix(base_ret, ret, test="fetch failure keeps the pool")
    assert [h["id"] for h in ret["hits"]] == base
    st = ret["session_reinjection_stats"]
    # the SEED census survives the fail-open: "seeded but the fetch failed"
    # is distinguishable from "nothing seeded" (falsifier 4's honest null)
    assert st["seed_sessions"] == [SR_A, SR_B]
    assert st["seeded"] == 2
    assert st["fetch_ok"] is False
    assert st["injected_total"] == 0 and st["injected_merged"] == 0


def test_zero_new_ids_skips_the_merge(seeded_sdk, monkeypatch):
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise import session_reinjection as _sr
    q = _question()
    base_ret = _base_pool(seeded_sdk, q)
    base = [h["id"] for h in base_ret["hits"]]
    monkeypatch.setattr(
        _sr, "source_session_chunk_pass",
        lambda *a, **kw: {"ok": True, "by_session": {}, "total": 0,
                          "dropped_by_cap": 0, "total_cap_hit": False})
    ret = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60, session_reinjection=True)
    _require_one_leg_mix(base_ret, ret, test="zero new ids keeps the pool")
    assert [h["id"] for h in ret["hits"]] == base
    st = ret["session_reinjection_stats"]
    assert st["fetch_ok"] is True
    assert st["injected_total"] == 0 and st["injected_merged"] == 0


def test_merge_stage_exception_keeps_the_base_pool(seeded_sdk, monkeypatch):
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise import session_reinjection as _sr
    q = _question()
    base_ret = _base_pool(seeded_sdk, q)
    base = [h["id"] for h in base_ret["hits"]]

    def _boom(*a, **kw):
        raise RuntimeError("merge blew up")

    monkeypatch.setattr(_sr, "reinjection_merge_order", _boom)
    ret = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60, session_reinjection=True)
    _require_one_leg_mix(base_ret, ret, test="merge exception keeps the pool")
    assert [h["id"] for h in ret["hits"]] == base
    st = ret["session_reinjection_stats"]
    # a MERGE-stage failure must not impersonate a graph outage: the FETCH
    # census survives (fetch_ok=True, injected_total>0) and only the MERGED
    # counters are cleared.
    assert st["fetch_ok"] is True
    assert st["injected_total"] >= 1
    assert st["injected_merged"] == 0
    assert st["injected_per_session_merged"] == {}


def test_floor_term_arm_reaches_the_reinjection_merge(seeded_sdk,
                                                    monkeypatch):
    """#2513 (#10629 review P2): the SECOND composition carries the arm too.

    The seam first landed armable from the direct composition only, so a
    re-injection A/B would have run with the lower bound OFF while the receipt
    said armed. Both compositions spend ``_floor_arm_guard_kwargs``; this pins
    the re-injection half by execution (the coverage-loop half lives in
    ``tests/test_coverage_loop.py``).
    """
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise import session_reinjection as _sr
    from tortoise.retrieval import DEFAULT_POOL_SESSION_CAP

    seen: list[dict] = []
    real = _sr.reinjection_merge_order

    def _capture(pool, added, **kw):
        seen.append({k: kw.get(k) for k in ("floor_term", "per_session_cap")})
        return real(pool, added, **kw)

    monkeypatch.setattr(_sr, "reinjection_merge_order", _capture)
    on = retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                               pool_size=60, session_reinjection=True,
                               floor_term=True, guard_per_session_cap=1)
    assert seen == [{"floor_term": True, "per_session_cap": 1}], seen
    st = on["session_reinjection_stats"]
    assert st["floor_term"] is True
    assert st["guard_per_session_cap"] == 1

    # the default path rides the SAME expression, with the product cap
    seen.clear()
    off = retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                                pool_size=60, session_reinjection=True)
    assert seen == [{"floor_term": False,
                     "per_session_cap": DEFAULT_POOL_SESSION_CAP}], seen
    assert off["session_reinjection_stats"]["floor_term"] is False


def test_one_batched_fetch_per_fired_question(seeded_sdk, monkeypatch):
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise import session_reinjection as _sr
    calls: list[list[str]] = []
    real = _sr.source_session_chunk_pass

    def _counting(proj, seed_point_ids, **kw):
        calls.append(list(seed_point_ids))
        return real(proj, seed_point_ids, **kw)

    monkeypatch.setattr(_sr, "source_session_chunk_pass", _counting)
    retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                          pool_size=60, session_reinjection=True)
    # ONE batched query, ALL seeds at once — anchored on the seeded POOL
    # HIT ids (not the session strings), which is what resolves the session
    assert len(calls) == 1
    assert sorted(calls[0]) == ["srAp0", "srBp0"]


def _pin_chunk_grain(monkeypatch) -> None:
    """Pin the C4 arm at the CHUNK kind for a test.

    The arm's default is now the product's TURN grain, and the C5 re-cap
    (``dedup_pool``) counts only ``is_raw_chunk`` hits — turn points are
    never chunk-capped (D3 #1540). The chunk-kind integration is therefore
    the only place the re-cap is exercisable end-to-end, and it is the same
    A/B ``source_session_chunk_pass``'s ``chunk_kind`` parameter exists for.
    """
    from tortoise import session_reinjection as _sr
    real = _sr.source_session_chunk_pass

    def _chunk_kind(proj, seed_point_ids, **kw):
        kw.setdefault("chunk_kind", SESSION_TRANSCRIPT_KIND)
        return real(proj, seed_point_ids, **kw)

    monkeypatch.setattr(_sr, "source_session_chunk_pass", _chunk_kind)


# ── (d) caps ─────────────────────────────────────────────────────────────

def test_c5_per_session_chunk_cap_holds_on_the_reinjected_pool(
        seeded_sdk, monkeypatch):
    from tools.longmem_eval.retrieve import retrieve_for_question
    _pin_chunk_grain(monkeypatch)
    for i in range(2, 6):
        seeded_sdk.create_point("session-transcript", CHUNK_CONTENT,
                                id=f"srAc{i}", session_id=SR_A,
                                status="draft")
    _link_sessions(seeded_sdk)
    ret = retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                                pool_size=60, session_reinjection=True,
                                max_chunks_per_session=2)
    n_chunks_a = sum(1 for h in ret["hits"]
                     if h.get("point_kind") == "session-transcript"
                     and h.get("session_id") == SR_A)
    assert n_chunks_a <= 2
    st = ret["session_reinjection_stats"]
    # the re-cap's OWN signal (not the fetch budget's dropped_by_cap):
    # fewer injected chunks survived than were merged in
    assert st["injected_merged"] < st["injected_total"]


def test_total_cap_knob_threads_and_defaults(seeded_sdk, monkeypatch):
    """The eval-side total-cap knob (#2513 cap sweep) reaches
    ``source_session_chunk_pass`` and defaults to the PRODUCT constant.

    The knob exists because the total budget is the only volume guard on
    injected turn points (the C5 re-cap does not bound them), so whether the
    shipped value truncates the arm is answerable only by sweeping it. It is
    measurement-only and eval-side: the product constant stays the shipped
    default, an OFF run never reads it, and a garbage/out-of-range value
    falls back to the constant (``rerank._env_int`` clamp) rather than
    crashing a measured run.
    """
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise import session_reinjection as _sr
    from tortoise.session_reinjection import DEFAULT_REINJECTION_TOTAL_ITEMS

    seen: list[int] = []
    real = _sr.source_session_chunk_pass

    def _capture(proj, seed_point_ids, **kw):
        seen.append(kw["total_cap"])
        return real(proj, seed_point_ids, **kw)

    monkeypatch.setattr(_sr, "source_session_chunk_pass", _capture)

    # unset → the product constant (never a ceiling, never a silent zero)
    monkeypatch.delenv("TORTOISE_LME_REINJECTION_TOTAL_CAP", raising=False)
    retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                          pool_size=60, session_reinjection=True)
    assert seen == [DEFAULT_REINJECTION_TOTAL_ITEMS]

    # explicit env wins
    monkeypatch.setenv("TORTOISE_LME_REINJECTION_TOTAL_CAP", "15")
    retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                          pool_size=60, session_reinjection=True)
    assert seen == [DEFAULT_REINJECTION_TOTAL_ITEMS, 15]

    # garbage / < 1 falls back to the constant — a measured run never crashes
    monkeypatch.setenv("TORTOISE_LME_REINJECTION_TOTAL_CAP", "garbage")
    retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                          pool_size=60, session_reinjection=True)
    monkeypatch.setenv("TORTOISE_LME_REINJECTION_TOTAL_CAP", "0")
    retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                          pool_size=60, session_reinjection=True)
    assert seen == [DEFAULT_REINJECTION_TOTAL_ITEMS, 15,
                    DEFAULT_REINJECTION_TOTAL_ITEMS,
                    DEFAULT_REINJECTION_TOTAL_ITEMS]

    # and an OFF run reads nothing at all (the arm gate comes first)
    monkeypatch.setenv("TORTOISE_LME_REINJECTION_TOTAL_CAP", "15")
    retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                          pool_size=60, session_reinjection=False)
    assert len(seen) == 4


def test_total_cap_run_resolved_value_wins_over_env(seeded_sdk, monkeypatch):
    """#2513 (delta-review P1): the run path resolves the cap ONCE and passes
    it explicitly — the env read is the direct-caller fallback only.

    A run whose fingerprint recorded cap 10 must serve 10 even if the env
    moves to 15 before the question runs; otherwise the artifact declares
    one config while the fetch serves another.
    """
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise import session_reinjection as _sr

    seen: list[int] = []
    real = _sr.source_session_chunk_pass

    def _capture(proj, seed_point_ids, **kw):
        seen.append(kw["total_cap"])
        return real(proj, seed_point_ids, **kw)

    monkeypatch.setattr(_sr, "source_session_chunk_pass", _capture)
    monkeypatch.setenv("TORTOISE_LME_REINJECTION_TOTAL_CAP", "15")
    retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                          pool_size=60, session_reinjection=True,
                          session_reinjection_total_cap=10)
    assert seen == [10]  # explicit run-resolved value, never the stray env

    # #2513 (delta-review P2): a direct caller's EXPLICIT value is clamped
    # through the SAME ``_clamp_int`` as the env fallback — the pre-fix
    # shape forwarded it verbatim, so 0 served a silent zero-injection arm
    # (arm ON, nothing injected, no error) and '15' raised a TypeError into
    # the fail-open handler. Both must now resolve exactly as the env case.
    from tortoise.session_reinjection import DEFAULT_REINJECTION_TOTAL_ITEMS
    for raw, expected in ((0, DEFAULT_REINJECTION_TOTAL_ITEMS),
                          (-3, DEFAULT_REINJECTION_TOTAL_ITEMS),
                          ("15", 15)):
        seen.clear()
        retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                              pool_size=60, session_reinjection=True,
                              session_reinjection_total_cap=raw)
        assert seen == [expected], raw


# ── (i) the PRODUCT turn shape: no ``session_id`` on the point ───────────

def test_product_shaped_turn_is_injected_via_its_session(seeded_sdk):
    """A product turn Point carries NO ``session_id``
    (``capture_session`` writes content/kind/speaker/is_episodic and links
    ``Session-[:CONTAINS]->Point``). The fetch's group key must therefore
    fall back to the ``:Session`` id — and the key must equal the seed's
    ``session_id`` (or the group is orphaned into the merge's defensive
    tail). The non-turn ``event`` (the pre-#10000 demo-seed shape) is NOT a
    turn."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    _create_product_turn(seeded_sdk, "srApv0",
                         TURN_PREFIX + OFF_POOL_TURN_CONTENT)
    _create_demo_event(seeded_sdk, "srAev0",
                       "[user] let's set up our agent memory system")
    _link_sessions(seeded_sdk, extra={SR_A: ["srApv0", "srAev0"]})
    ret = retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                                pool_size=60, session_reinjection=True)
    st = ret["session_reinjection_stats"]
    ids = {h["id"] for h in ret["hits"]}
    assert st["fetch_ok"] is True
    # the ``session_id``-less turn is injected and grouped under SR_A (its
    # Session id) — never under ``""`` and never orphaned: every injected
    # item survives the merge.
    assert st["injected_per_session"] == {SR_A: 3}
    assert st["injected_merged"] == 3
    assert {"srAq0", "srAq1", "srApv0"} <= ids
    # the pre-#10000 demo-seed ``event`` shares the KIND but fails the SHAPE
    # (``is_episodic`` absent) — it must not be injected
    assert "srAev0" not in ids


# ── (e) TR exclusion ─────────────────────────────────────────────────────

def test_tr_questions_are_excluded(seeded_sdk):
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question(question_type="temporal-reasoning")
    ret = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60, session_reinjection=True)
    st = ret["session_reinjection_stats"]
    assert st["tr_excluded"] is True
    assert st["seeded"] == 0
    assert st["injected_total"] == 0


# ── (f) single-session pool: guard is a no-op ────────────────────────────

def test_guard_is_a_noop_on_a_single_session_pool(monkeypatch):
    monkeypatch.setenv("TORTOISE_DB_URI", _fresh_uri())
    sdk = TortoiseSDK()
    try:
        _seed_graph(sdk, n_a=3, n_chunks=2)
        sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.session_id = $b DETACH DELETE p",
            params={"b": SR_B})
        from tools.longmem_eval.retrieve import retrieve_for_question
        q = _question(answer_sessions=[SR_A])
        on = retrieve_for_question(sdk, q, ks=(5,), top_k=10, pool_size=60,
                                   session_reinjection=True)
        off = retrieve_for_question(sdk, q, ks=(5,), top_k=10, pool_size=60,
                                    session_reinjection=True,
                                    session_reinjection_guard=False)
        _require_one_leg_mix(on, off, test="guard-off re-cap parity")
        assert [h["id"] for h in on["hits"]] == \
            [h["id"] for h in off["hits"]]
    finally:
        sdk.close()


# ── (g) guard OFF still re-caps through the shared contract ──────────────

def test_guard_off_still_recaps(seeded_sdk, monkeypatch):
    from tools.longmem_eval.retrieve import retrieve_for_question
    _pin_chunk_grain(monkeypatch)
    for i in range(2, 8):
        seeded_sdk.create_point("session-transcript", CHUNK_CONTENT,
                                id=f"srAc{i}", session_id=SR_A,
                                status="draft")
    _link_sessions(seeded_sdk)
    # max_chunks_per_session=2 is TIGHTER than the fetch budget (3), so the
    # retained count can only be 2 if the re-cap actually ran.
    ret = retrieve_for_question(
        seeded_sdk, _question(), ks=(5,), top_k=10, pool_size=60,
        session_reinjection=True, session_reinjection_guard=False,
        max_chunks_per_session=2)
    n_chunks_a = sum(1 for h in ret["hits"]
                     if h.get("point_kind") == "session-transcript"
                     and h.get("session_id") == SR_A)
    assert n_chunks_a == 2


# ── (h) census keys + env gate + CLI + abort ────────────────────────────

_CENSUS_KEYS = {
    "on", "seed_window", "seed_limit", "seed_sessions", "seeded",
    "injected_per_session", "injected_total",
    "injected_per_session_merged", "injected_merged", "dropped_by_cap",
    "fetch_ok", "total_cap_hit", "guard", "latency_ms", "tr_excluded",
    # #2513 (#10629 review): the arm THIS composition spent — recorded per
    # outcome so a re-injection run's receipt says whether the lower bound
    # was armed and under which window cap, not only what the run declared.
    "floor_term", "guard_per_session_cap",
    # #10540: the reader-window crowding census — the pool-scope keys above
    # could not see it (a displaced item is not dropped by any pool cap).
    "reader_window_displacement"}


def test_reader_item_and_token_caps_hold_on_the_reinjected_pool(seeded_sdk):
    """Task-3 acceptance (d): the reader item/token caps still bound the
    context AFTER injection — a re-injected pool must not smuggle items
    past the reader window (the C4 injected items are pool entries, not a
    bypass of the reader budget)."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question()
    cap_items, cap_tokens = 12, 900
    for arm_on in (False, True):
        ret = retrieve_for_question(
            seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
            session_reinjection=arm_on, context_item_cap=cap_items,
            max_context_tokens=cap_tokens)
        assert ret["context_point_count"] <= cap_items, (arm_on, ret)
        assert ret["context_tokens"] <= cap_tokens, (arm_on, ret)
        assert len(ret["hits"]) <= cap_items, (arm_on, len(ret["hits"]))
        # the anti-desync derivation: the seed window tracks the RESOLVED
        # reader item cap (reverting it to the product constant fails here)
        assert ret["session_reinjection_stats"]["seed_window"] == cap_items


def test_seed_window_defaults_to_the_product_constant(seeded_sdk):
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise.session_reinjection import DEFAULT_REINJECTION_SEED_WINDOW
    ret = retrieve_for_question(
        seeded_sdk, _question(), ks=(5,), top_k=10, pool_size=60,
        session_reinjection=True)
    assert (ret["session_reinjection_stats"]["seed_window"]
            == DEFAULT_REINJECTION_SEED_WINDOW)


def test_census_key_set_is_pinned(seeded_sdk):
    from tools.longmem_eval.retrieve import retrieve_for_question
    ret = retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                                pool_size=60, session_reinjection=True)
    assert set(ret["session_reinjection_stats"]) == _CENSUS_KEYS


def test_eval_env_gate_failsafe_off(seeded_sdk, monkeypatch):
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question()
    monkeypatch.delenv("TORTOISE_LME_SESSION_REINJECTION", raising=False)
    assert retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10,
        pool_size=60)["session_reinjection"] is False
    monkeypatch.setenv("TORTOISE_LME_SESSION_REINJECTION", "garbage")
    assert retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10,
        pool_size=60)["session_reinjection"] is False
    monkeypatch.setenv("TORTOISE_LME_SESSION_REINJECTION", "yes")
    assert retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10,
        pool_size=60)["session_reinjection"] is True
    # explicit flag beats the env in both directions
    monkeypatch.delenv("TORTOISE_LME_SESSION_REINJECTION", raising=False)
    assert retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
        session_reinjection=False)["session_reinjection"] is False
    monkeypatch.setenv("TORTOISE_LME_SESSION_REINJECTION", "1")
    assert retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
        session_reinjection=False)["session_reinjection"] is False


def test_both_arms_on_aborts_with_message_and_nonzero_exit(
        tmp_path, monkeypatch, capsys):
    """§0.2: the refusal is raised at ARM RESOLUTION and ABORTS the run —
    the clean message + a non-zero exit, never per-question failures."""
    from tools.longmem_eval import run as _run
    monkeypatch.delenv("TORTOISE_LME_COVERAGE_LOOP", raising=False)
    monkeypatch.delenv("TORTOISE_LME_SESSION_REINJECTION", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        _run.run_main([
            "--data", str(MINI), "--limit", "1", "--split", "s", "--mock",
            # #4718: the module's autouse _no_embedder fixture pins the dense
            # leg out, so the waiver must be explicit — --mock no longer
            # silently waives it. The arm-conflict abort under test is
            # independent of the dense leg.
            "--skip-preflight",
            "--coverage-loop", "--session-reinjection",
            "--output", str(tmp_path / "r.json")])
    assert excinfo.value.code == 1
    assert "arm conflict" in capsys.readouterr().err
    assert not (tmp_path / "r.json").exists()


def test_floor_term_cli_flags_reach_the_methodology(monkeypatch, tmp_path):
    """#2513 (#10629 review P2): the flags the published A/B command passes
    EXIST and are recorded (methodology == actual).

    ``--floor-term`` / ``--no-floor-term`` are tri-state (explicit beats the
    env) and ``--guard-per-session-cap`` reaches the guard's cap; arming
    either without a pool-order arm is REFUSED (exit 1), never a silent
    no-op.
    """
    from tools.longmem_eval import run as _run

    base = ["--data", str(MINI), "--limit", "1", "--split", "s", "--mock",
            "--skip-preflight", "--output", str(tmp_path / "r.json")]
    monkeypatch.delenv("TORTOISE_LME_FLOOR_TERM", raising=False)
    rep = _run.run_main([*base, "--session-reinjection", "--floor-term",
                         "--guard-per-session-cap", "3"])
    k = rep["methodology"]
    assert k["floor_term"] is True
    assert k["guard_per_session_cap"] == 3

    # tri-state: an explicit --no-floor-term beats the env twin
    monkeypatch.setenv("TORTOISE_LME_FLOOR_TERM", "1")
    rep = _run.run_main([*base, "--session-reinjection", "--no-floor-term"])
    k = rep["methodology"]
    assert k["floor_term"] is False
    assert k["guard_per_session_cap"] == DEFAULT_POOL_SESSION_CAP

    # no pool-order arm → the guard never runs → the run REFUSES the knobs
    monkeypatch.delenv("TORTOISE_LME_FLOOR_TERM", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        _run.run_main([*base, "--floor-term"])
    assert excinfo.value.code == 1


def test_run_level_tristate_and_methodology(monkeypatch, tmp_path):
    """CLI flag > env > OFF at the run level, and the resolved arm + guard
    are recorded in the methodology (methodology == actual)."""
    from tools.longmem_eval import run as _run
    # #4718: the autouse _no_embedder fixture pins the dense leg out; the
    # dense-leg waiver is now explicit (--mock alone no longer grants it).
    base = ["--data", str(MINI), "--limit", "1", "--split", "s", "--mock",
            "--skip-preflight", "--output", str(tmp_path / "r.json")]
    monkeypatch.setenv("TORTOISE_LME_SESSION_REINJECTION", "1")
    monkeypatch.delenv("TORTOISE_LME_COVERAGE_LOOP", raising=False)
    rep = _run.run_main([*base, "--no-session-reinjection"])
    k = rep["methodology"]
    assert k["session_reinjection"] is False     # explicit flag beats env
    monkeypatch.delenv("TORTOISE_LME_SESSION_REINJECTION", raising=False)
    rep = _run.run_main([*base, "--session-reinjection",
                         "--no-session-reinjection-guard"])
    k = rep["methodology"]
    assert k["session_reinjection"] is True
    assert k["session_reinjection_guard"] is False


# ── the census window is the surface the arm actually delivers ──────────


@pytest.fixture
def deep_seeded_sdk(seeded_sdk):
    """``seeded_sdk`` plus ten more off-pool turns on session A.

    The default fixture injects fewer items than ``top_k``, so a window that
    is too LARGE and a window that is right agree on every count — the defect
    below is invisible there. Ten extra turns make the merged pool longer than
    the reader's ``top_k``, which is the only shape in which "did the census
    count what the reader received?" has two different answers.
    """
    for i in range(2, 12):
        seeded_sdk.create_point("event", TURN_PREFIX + OFF_POOL_TURN_CONTENT,
                                id=f"srAq{i}", session_id=SR_A,
                                speaker="user", is_episodic=True,
                                status="draft")
    _link_sessions(seeded_sdk)
    return seeded_sdk


def test_census_failure_is_UNMEASURED_and_does_not_decide_the_arm(
        seeded_sdk, monkeypatch):
    """#10540: the reader-window census is a MEASUREMENT, so its failure must
    not decide whether the treatment is applied.

    The census has its own ``except`` and sits AFTER ``pool = _merged``, and
    the two halves of that are what this pins — they fail DIFFERENTLY:

      * if it is moved back onto the arm's decision path, the merge reverts to
        the control pool and the injected ids vanish from ``hits``;
      * if its exception is instead allowed to reach the OUTER fail-open
        handler, the merged pool still stands (``hits`` keep the injected
        ids) and only the MERGED counters are cleared — that handler's own
        contract is "the SEED and FETCH censuses are NOT wiped", so
        ``fetch_ok`` is deliberately still True on that path.

    Where its own ``except`` handles the failure — the path this test
    measures — the census must read the UNMEASURED shape (``measured: False``
    and ``None`` values), never the zeroed one: a zero there would be
    indistinguishable from "no crowding" and from a merge-stage failure at
    the same time. (On the outer-handler branch the census is simply never
    written and keeps its initialised zeroed shape — ``measured: False`` since
    the never-attempted path stopped claiming a measurement — so this key's
    assertion fails with it on the ``None``-vs-``0`` values; the COUNTERS are
    what identify the branch (they clear to 0 while ``hits`` still carry the
    injected ids).)
    """
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise import session_reinjection as _sr

    q = _question()
    measure = dict(ks=(5,), top_k=10, pool_size=60,
                   session_reinjection=True)
    # the same run with the census WORKING is the counterfactual: a census
    # failure may change the census key and the LATENCY, and no other
    # OUTCOME key.
    ok = retrieve_for_question(seeded_sdk, q, **measure)
    st_ok = ok["session_reinjection_stats"]
    assert st_ok["reader_window_displacement"]["measured"] is True
    assert st_ok["injected_merged"] > 0

    def _boom(*a, **kw):
        raise RuntimeError("census blew up")

    monkeypatch.setattr(_sr, "reader_window_displacement", _boom)
    ret = retrieve_for_question(seeded_sdk, q, **measure)
    st = ret["session_reinjection_stats"]
    # (1) the ARM was still applied, byte-identically: a census failure did
    # not revert the pool, clear the merged counters, or let its exception
    # reach the outer fail-open handler (which would clear ``injected_merged``
    # to 0 and leave the initialised zeroed census shape instead).
    # ``fetch_ok`` is never CLEARED by either handler — the outer handler's
    # contract is "only the MERGED counters are cleared" — so on this path it
    # stays True. It is not True on EVERY fail-open path: a fetch that itself
    # reports not-ok leaves it False (``test_fetch_failure_keeps_the_base_pool``).
    # So this assertion documents that contract rather than discriminating a
    # branch.
    assert st["fetch_ok"] is True
    assert [h["id"] for h in ret["hits"]] == [h["id"] for h in ok["hits"]]
    assert st["injected_merged"] == st_ok["injected_merged"] > 0
    assert (st["injected_per_session_merged"]
            == st_ok["injected_per_session_merged"] != {})
    assert ({h["id"] for h in ret["hits"]}
            == {h["id"] for h in ok["hits"]})
    # (2) the census records UNMEASURED, not a zero that reads as "no
    # crowding" — ``window`` still reports what was attempted
    assert st["reader_window_displacement"] == {
        "window": 40, "injected_in_window": None, "base_displaced": None,
        "sessions_lost": None, "sessions_before": None,
        "sessions_after": None, "measured": False}


def test_an_unrun_census_is_never_reported_as_a_measured_zero(
        seeded_sdk, monkeypatch):
    """#10540 (review P2): ``measured`` is ``True`` IFF the census actually ran.

    The zeroed shape is minted BEFORE the arm decides, so it is what ships on
    every path that never reaches the census: the whole OFF arm (half of every
    A/B run), every TR question, and an ON-arm question whose fetch injected
    nothing (plus a not-ok fetch and a merge-stage failure, which have their
    own tests). On those paths ``sessions_before == 0`` is a PLACEHOLDER —
    the shipped comment two lines above the shape says exactly that — so
    ``measured: True`` makes three distinct states report one value: "the
    census ran and found no crowding", "no census was taken", and "the arm
    did nothing at all".

    Two consequences the flag exists to prevent, both silent: a consumer that
    filters on ``measured`` (the documented way to read this key) folds half a
    corpus's OFF-arm questions into the crowding sample, and a consumer that
    computes ``len(sessions_lost) / sessions_before`` divides by zero on 100%
    of the OFF arm. Pre-fix this test fails on the FIRST assertion with
    ``measured: True``; the counts asserted beside it are what keep the fix
    minimal — the arm delta the shipped comment protects stays byte-identical.

    The counterfactual (``measured: True`` with real counts) is not re-taken
    here: ``test_census_failure_is_UNMEASURED_and_does_not_decide_the_arm``
    reads it on the SAME question with the arm ON.
    """
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise import session_reinjection as _sr

    def _census(**kwargs):
        ret = retrieve_for_question(seeded_sdk, ks=(5,), top_k=10,
                                    pool_size=60, **kwargs)
        return ret["session_reinjection_stats"]["reader_window_displacement"]

    off = _census(question=_question(), session_reinjection=False)
    tr = _census(question=_question(question_type="temporal-reasoning"),
                 session_reinjection=True)
    monkeypatch.setattr(
        _sr, "source_session_chunk_pass",
        lambda *a, **kw: {"ok": True, "by_session": {}, "total": 0,
                          "dropped_by_cap": 0, "total_cap_hit": False})
    nothing_injected = _census(question=_question(), session_reinjection=True)

    for name, d in (("off", off), ("tr", tr),
                    ("on-but-nothing-injected", nothing_injected)):
        assert d["measured"] is False, (name, d)
        # the VALUES are untouched by the fix: a zeroed design, not a
        # measurement — so the per-question arm delta stays reconstructable
        assert (d["sessions_before"], d["sessions_after"]) == (0, 0), name
        assert d["sessions_lost"] == [] and d["base_displaced"] == 0, name
        assert d["injected_in_window"] == 0, name
    # ``window`` still reports what WOULD have been censused — the reader's
    # resolved item cap, which for a TR question is the pinned ``tr_top_k``
    # (the arm is excluded there, so the number is the only honest one)
    from tools.longmem_eval.retrieve import DEFAULT_TR_TOP_K
    assert off["window"] == 40, off["window"]
    assert tr["window"] == DEFAULT_TR_TOP_K, tr["window"]




def test_the_pool_only_arm_censuses_the_prefix_the_reader_receives(
        deep_seeded_sdk):
    """#10540: on the POOL-ONLY isolation arm the census window is ``top_k``.

    That arm (``--rerank off --rerank-pool N``) fetches a DEEP pool and then
    truncates the delivered surface to ``top_k`` before assembly — the OQ5
    reader invariant (``pool = pool[:top_k]`` in ``retrieve_for_question``) —
    while the assembled item cap stays ``eff_item_cap`` (40). So the reader
    receives ``merged[:top_k]`` and a census window of ``eff_item_cap``
    counts items it never received: crowding over-reported on precisely the
    arm a pool-depth sweep reads it on.

    The truncation is a PREFIX cut, so narrowing the window to
    ``min(eff_item_cap, top_k)`` is the tightest window that arm admits: the
    count becomes a CEILING there (nothing below the cut can reach the
    reader) — not the delivered set, because a skipped hit inside the prefix
    still over-counts — and it UNDER-counts whenever ``top_k >
    eff_item_cap``, because the cut then sits above the window and a skipped
    hit frees a slot the reader walks past it with (the same under-count as
    every other arm; ``retrieve.py`` states the condition). That narrowing is
    not available to the R6 rerank arm, whose ``pool = selected`` re-SELECTS
    from the whole pool.

    ⛔ The window is derived from the truncation SHAPE, not from the
    pool-only FLAG (#10632 review ISSUE 1): the same prefix cut happens on
    the two R6 degrade arms, which no flag here can see — see
    ``test_the_degrade_arms_census_the_prefix_the_reader_receives``.

    Non-vacuity: pre-fix the caller reports ``window: 40`` on this arm, so the
    first assertion reds (``assert 40 == 8``); pre-fix every injected item is
    inside 40, so ``injected_in_window`` also equals ``injected_merged`` and
    the second assertion reds with it. The DEFAULT arm is measured beside
    them so the narrowing is attributable to this arm and not to a global
    window change.
    """
    from tools.longmem_eval.retrieve import retrieve_for_question

    q = _question()
    top_k = 8

    def _run(**kw):
        return retrieve_for_question(
            deep_seeded_sdk, q, ks=(5,), top_k=top_k, pool_size=60,
            session_reinjection=True, **kw)

    default = _run()
    st_default = default["session_reinjection_stats"]
    pool_only = _run(rerank=False, rerank_pool=40)
    st_only = pool_only["session_reinjection_stats"]

    # the arm under test is the pool-only one, and it really truncated: on
    # this fixture the final pool IS the top_k prefix, which is the surface
    # the window below must describe
    assert st_only["injected_merged"] > 0, st_only["injected_merged"]
    assert len(pool_only["hits"]) == top_k, (
        "the pool-only arm's delivered surface must be the top_k prefix — "
        "if this changes, the census window below is measuring the wrong "
        f"surface (got {len(pool_only['hits'])} hits)")

    census = st_only["reader_window_displacement"]
    assert census["measured"] is True
    # (1) the window is the arm's truncated surface, not the item cap
    #     (pre-fix: 40)
    assert census["window"] == len(pool_only["hits"]) == top_k
    # (2) therefore injected items beyond the cut are NOT counted as
    #     crowding the reader. Pre-fix every injected item sits inside the
    #     40-window, so this strictness is what the widening hides.
    assert census["injected_in_window"] < st_only["injected_merged"], (
        "the census counted injected items the reader never received: "
        f"in_window={census['injected_in_window']} "
        f"merged={st_only['injected_merged']}")
    assert 0 <= census["injected_in_window"] <= top_k

    # the DEFAULT arm is untouched: its window stays the item cap (40), and
    # nothing merged sits outside it
    assert st_default["reader_window_displacement"]["window"] == 40
    assert (st_default["reader_window_displacement"]["injected_in_window"]
            == st_default["injected_merged"])


def test_the_degrade_arms_census_the_prefix_the_reader_receives(
        deep_seeded_sdk, monkeypatch):
    """#10632 review ISSUE 1 (P2): the DEGRADE arms cut the same prefix.

    ``retrieve_for_question`` truncates the delivered pool to ``top_k`` at
    THREE sites: the pool-only arm, the D3(a)/S25 load-failure degrade
    (``get_scorer`` returns ``None``) and the D8c score-failure degrade
    (``rerank_hits`` reports ``applied: False``). The last two are decided by
    the R6 stage, so the flag test that narrowed the window for the pool-only
    arm could not see them: the window stayed the reader's item cap (40)
    while the reader received at most ``top_k`` — so ``injected_in_window`` /
    ``base_displaced`` over-reported crowding, and ``sessions_lost`` could
    name a session as lost that the reader never had, all stamped
    ``measured: True``. The window now follows the truncation SHAPE: each
    cutting site declares its cut and the census is taken after R6.

    Non-vacuity: pre-fix both arms report ``window: 40`` against a
    ``len(hits) == top_k == 8`` surface, so the first assertion reds; and
    every injected item sits inside 40, so ``injected_in_window`` equals
    ``injected_merged`` and the second reds too. The applied arm is measured
    beside them as the CONTROL: it re-selects rather than cuts a prefix, so
    narrowing it would under-count every item the reranker lifted from below
    the window — its window must stay the resolved item cap.
    """
    from tools.longmem_eval import rerank as _rerank
    from tools.longmem_eval.retrieve import retrieve_for_question

    q = _question()
    top_k = 8

    class _Boom:
        def score(self, query, contents):
            raise RuntimeError("predict exploded mid-run")

    def _census(monkeypatch, arm, scorer_result):
        monkeypatch.setattr(_rerank, "get_scorer",
                            lambda model=None: scorer_result)
        ret = retrieve_for_question(
            deep_seeded_sdk, q, ks=(5,), top_k=top_k, pool_size=60,
            session_reinjection=True, rerank=True)
        assert ret["rerank_pass"]["applied"] is False, arm
        # precondition: the reader's surface really IS the cut prefix, so a
        # window wider than len(hits) censuses items nobody received
        assert len(ret["hits"]) == top_k, (arm, len(ret["hits"]))
        census = ret["session_reinjection_stats"]["reader_window_displacement"]
        assert census["measured"] is True, (arm, census)
        # (1) the window is the surface the reader receives, not the item cap
        assert census["window"] == len(ret["hits"]) == top_k, (arm, census)
        # (2) therefore items below the cut are NOT counted as crowding
        injected_merged = ret["session_reinjection_stats"]["injected_merged"]
        assert 0 < census["injected_in_window"] < injected_merged, (
            arm, census, injected_merged)
        return census

    load_failure = _census(monkeypatch, "load-failure degrade",
                           (None, "load failed: boom"))
    score_failure = _census(monkeypatch, "score-failure degrade",
                            (_Boom(), ""))
    assert score_failure["window"] == load_failure["window"] == top_k

    # CONTROL: the applied arm re-selects from the whole pool, so no prefix
    # describes what the reader received — its window stays the item cap
    monkeypatch.setattr(_rerank, "get_scorer",
                        lambda model=None: (_rerank.FakeScorer(), ""))
    applied = retrieve_for_question(
        deep_seeded_sdk, q, ks=(5,), top_k=top_k, pool_size=60,
        session_reinjection=True, rerank=True)
    assert applied["rerank_pass"]["applied"] is True
    assert (applied["session_reinjection_stats"]["reader_window_displacement"]
            ["window"] == 40)


def test_the_census_window_is_the_readers_item_cap_not_the_pool(
        seeded_sdk, monkeypatch):
    """#10540 (review P2): the WIRING is pinned, not only the pure function.

    Every value assertion the census had before this test lived on the OFF /
    TR / nothing-injected paths or on ``reader_window_displacement`` called
    directly, so the wiring could be changed to census the POOL and nothing
    reddened: ``window=len(_merged)`` leaves ``measured: True``, leaves
    ``hits`` byte-identical, and (on the fixtures above, where the pool is
    SHORTER than the reader cap) reports the same numbers, because there the
    slice and the pool are the same list. That is the exact pre-#10540
    defect — a pool-scope count wearing the reader-window key's name — and
    the tests that exist to keep it out could not see it.

    So the window is read at the CALL SITE, resolved SMALLER than the merged
    pool so the two readings must diverge, and both the argument and the
    resulting values are asserted. ``context_item_cap=3`` against a 9-item
    merged pool: the reader's prefix is 3 items of one session, so
    ``injected_in_window`` is 0 and ``sessions_before`` is 1. A pool-scope
    census reports ``window: 9``, ``injected_in_window: 2`` and
    ``sessions_before: 2`` on the same question — it reds on all three.

    This complements, and does not replace, the pure-function tests: those
    pin the prefix SEMANTICS, this pins that the semantics are reached with
    the reader's cap. The seeding consequence of a tight cap (only session A
    is in the 3-item seed window, so only A injects) is not what is under
    test and is asserted only as a precondition.
    """
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise import session_reinjection as _sr

    seen: dict = {}
    real = _sr.reader_window_displacement

    def _spy(base_pool, merged, **kwargs):
        seen["window"] = kwargs.get("window")
        seen["merged_len"] = len(merged)
        seen["base_len"] = len(base_pool)
        return real(base_pool, merged, **kwargs)

    monkeypatch.setattr(_sr, "reader_window_displacement", _spy)
    tight = retrieve_for_question(
        seeded_sdk, _question(), ks=(5,), top_k=10, pool_size=60,
        context_item_cap=3, session_reinjection=True)
    d = tight["session_reinjection_stats"]["reader_window_displacement"]

    # precondition: the fixture actually separates the two readings — a pool
    # that is SHORTER than the cap would make a pool-scope mutant a no-op
    assert seen["window"] == 3, seen
    assert seen["merged_len"] > seen["window"], seen
    assert seen["base_len"] > seen["window"], seen

    assert d["window"] == 3, d
    assert d["measured"] is True, d
    # window-scope values, and each is DIFFERENT from the pool-scope count
    assert d["injected_in_window"] == 0, d
    assert d["sessions_before"] == 1, d
    # the default cap still resolves to the reader's 40 — the arm's own
    # control, so a hardcoded 3 cannot pass this pair either
    wide = retrieve_for_question(
        seeded_sdk, _question(), ks=(5,), top_k=10, pool_size=60,
        session_reinjection=True)
    w = wide["session_reinjection_stats"]["reader_window_displacement"]
    assert w["window"] == 40, w
    assert seen["window"] == 40, seen

