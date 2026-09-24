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
        merge-stage exception each return the base pool unchanged,
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
        ``:Session`` id, and a non-turn ``pointKind='event'`` point (the
        hosted demo seed's shape) is NOT — the fixture's ``CONTAINS``
        membership is per session, so a cross-session leak is visible here.

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
from tortoise.retrieval import SESSION_TRANSCRIPT_KIND
from tortoise.sdk import TortoiseSDK

MINI = Path(__file__).resolve().parent / "fixtures" / "longmemeval_mini.json"


# ── Live-FalkorDB availability (the FTS backend the arm needs) ───────────
def _falkordb_available() -> bool:
    """Probe a live FalkorDB; reads TORTOISE_DB_URI at CALL time so the
    module never captures it at import (#221 test-isolation lint)."""
    uri = os.environ.get(
        "TORTOISE_DB_URI",
        "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")
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
        "TORTOISE_DB_URI",
        "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")


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


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the dense leg OUT (hermetic): the differential isolates the
    sparse pool mechanics. No model load, no network.

    Also pin the ARM ENV to unset: the OFF-arm tests assert byte-identity
    against the documented default, so an ambient
    ``TORTOISE_LME_SESSION_REINJECTION`` / ``TORTOISE_LME_CONTEXT_ITEMS``
    (which resizes the derived seed window) would silently flip the arm
    under test."""
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get",
                        staticmethod(lambda: None))
    monkeypatch.delenv("TORTOISE_LME_SESSION_REINJECTION", raising=False)
    monkeypatch.delenv("TORTOISE_LME_CONTEXT_ITEMS", raising=False)


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
    """The hosted demo/dashboard seed's shape: ``pointKind='event'`` with
    NO ``is_episodic``. Its body IS role-tagged (``hosted_api.
    _DEMO_EPISODIC_TURNS``), so ``is_episodic`` is the conjunct that
    excludes it — not the ``[``-prefix test."""
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
    # off-arm census is the zeroed shape (reconstructable per question)
    assert off["session_reinjection"] is False
    st_off = off["session_reinjection_stats"]
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
    assert [h["id"] for h in default["hits"]] == [h["id"] for h in off["hits"]]
    assert default["session_reinjection"] is False
    assert default["session_reinjection_stats"] == \
        off["session_reinjection_stats"]


# ── (c) fail-open: fetch failure / zero new ids / merge exception ────────

def _base_ids(sdk, q):
    from tools.longmem_eval.retrieve import retrieve_for_question
    return [h["id"] for h in retrieve_for_question(
        sdk, q, ks=(5,), top_k=10, pool_size=60)["hits"]]


def test_fetch_failure_keeps_the_base_pool(seeded_sdk, monkeypatch):
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise import session_reinjection as _sr
    q = _question()
    base = _base_ids(seeded_sdk, q)

    def _boom(*a, **kw):
        raise RuntimeError("fetch blew up")

    monkeypatch.setattr(_sr, "source_session_chunk_pass", _boom)
    ret = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60, session_reinjection=True)
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
    base = _base_ids(seeded_sdk, q)
    monkeypatch.setattr(
        _sr, "source_session_chunk_pass",
        lambda *a, **kw: {"ok": True, "by_session": {}, "total": 0,
                          "dropped_by_cap": 0, "total_cap_hit": False})
    ret = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60, session_reinjection=True)
    assert [h["id"] for h in ret["hits"]] == base
    st = ret["session_reinjection_stats"]
    assert st["fetch_ok"] is True
    assert st["injected_total"] == 0 and st["injected_merged"] == 0


def test_merge_stage_exception_keeps_the_base_pool(seeded_sdk, monkeypatch):
    from tools.longmem_eval.retrieve import retrieve_for_question
    from tortoise import session_reinjection as _sr
    q = _question()
    base = _base_ids(seeded_sdk, q)

    def _boom(*a, **kw):
        raise RuntimeError("merge blew up")

    monkeypatch.setattr(_sr, "reinjection_merge_order", _boom)
    ret = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60, session_reinjection=True)
    assert [h["id"] for h in ret["hits"]] == base
    st = ret["session_reinjection_stats"]
    # a MERGE-stage failure must not impersonate a graph outage: the FETCH
    # census survives (fetch_ok=True, injected_total>0) and only the MERGED
    # counters are cleared.
    assert st["fetch_ok"] is True
    assert st["injected_total"] >= 1
    assert st["injected_merged"] == 0
    assert st["injected_per_session_merged"] == {}


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
    tail). The non-turn ``event`` (demo-seed shape) is NOT a turn."""
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
    # the demo-seed ``event`` shares the KIND but fails the SHAPE
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
    "fetch_ok", "total_cap_hit", "guard", "latency_ms", "tr_excluded"}


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
