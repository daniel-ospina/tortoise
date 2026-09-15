"""#2517 (C4, #2513) — source-session re-injection: arm wiring + E2E.

The reader-surface + pool-rank-cut lever. Product rules live in
``tortoise/session_reinjection.py``; ``tools/longmem_eval/retrieve.py``
composes SEED → EXPAND → MERGE on the annotated pool, inserted AFTER the
C3-1 block and BEFORE the C2 boost.

Hermetic contract proven here (docker lane, dense leg pinned out):

  * (a) the END-TO-END flip: a seeded session's injected chunks trigger the
        shared guard, which admits a pool-present starved session into
        ``hits[:5]`` (``session_recall@5`` 0.5 → 1.0); with the guard
        ablated OFF the flip does NOT happen — the flip is attributable to
        the guard, not the fetched chunks (falsifier 1),
  * (b) default OFF == explicit OFF, byte-identical ranked ids,
  * (c) fail-open: a fetch failure, a zero-new-ids fetch, AND a forced
        merge-stage exception each return the base pool unchanged,
  * (d) caps: the C5 per-session raw-chunk cap holds on the re-injected
        pool,
  * (e) TR questions are excluded,
  * (f) the guard is a no-op on a single-session pool,
  * (g) guard OFF still re-caps through the same shared contract,
  * (h) the census keys, the resolved-arm env gate, the CLI flags, and the
        both-arms-ON ABORT (message + exit). The fingerprint-refusal and
        arm-conflict gates are NOT here: they are hermetic run-level gates
        and live in ``tests/test_session_reinjection_rules.py``, because
        this module skips as a whole on an unavailable FalkorDB probe.

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
#: the chunks carry NO query token, so plain FTS never puts them in the
#: pool — they are genuinely-new material for the C4 fetch.
CHUNK_CONTENT = ("in the afternoon we watched a documentary about "
                 "volcanoes and then went to bed early")


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


def _seed_graph(sdk, *, n_a: int = 6, n_chunks: int = 2) -> None:
    for i in range(n_a):
        sdk.create_point("event", MATCH_CONTENT, id=f"srAp{i}",
                         session_id=SR_A, status="draft")
    sdk.create_point("event", MATCH_CONTENT, id="srBp0",
                     session_id=SR_B, status="draft")
    for i in range(n_chunks):
        sdk.create_point("session-transcript", CHUNK_CONTENT,
                         id=f"srAc{i}", session_id=SR_A, status="draft")
    # the C4 fetch is question-scoped (`p.lme_question_id = $q`) — the eval
    # ingest writes it; direct create_point does not.
    _stamp_chunk_props(sdk)


def _stamp_chunk_props(sdk) -> None:
    """Stamp the eval's question-scoped props on the fixture's points
    (``lme_question_id``; ``lme_chunk_index`` for the raw chunks)."""
    sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.session_id IN $sids "
        "SET p.lme_question_id = $q, "
        "    p.lme_chunk_index = CASE WHEN p.pointKind = $kind "
        "        THEN toInteger(replace(p.id, 'srAc', '')) ELSE -1 END",
        params={"sids": [SR_A, SR_B], "q": QID,
                "kind": "session-transcript"})


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
    # window (40) — seeded too, but it owns no chunks to fetch
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
    # the injected chunks carry the driver's OWN leg
    assert any(h["match_source"] == "session" for h in on["hits"])


def test_flip_is_attributable_to_the_guard_not_the_chunks(seeded_sdk):
    """Falsifier 1: with the guard ablated OFF, the injected chunks still
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

    def _counting(proj, session_ids, **kw):
        calls.append(list(session_ids))
        return real(proj, session_ids, **kw)

    monkeypatch.setattr(_sr, "source_session_chunk_pass", _counting)
    retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                          pool_size=60, session_reinjection=True)
    assert calls == [[SR_A, SR_B]]    # ONE batched query, all seeds at once


# ── (d) caps ─────────────────────────────────────────────────────────────

def test_c5_per_session_chunk_cap_holds_on_the_reinjected_pool(seeded_sdk):
    from tools.longmem_eval.retrieve import retrieve_for_question
    for i in range(2, 6):
        seeded_sdk.create_point("session-transcript", CHUNK_CONTENT,
                                id=f"srAc{i}", session_id=SR_A,
                                status="draft")
    _stamp_chunk_props(seeded_sdk)
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

def test_guard_off_still_recaps(seeded_sdk):
    from tools.longmem_eval.retrieve import retrieve_for_question
    for i in range(2, 8):
        seeded_sdk.create_point("session-transcript", CHUNK_CONTENT,
                                id=f"srAc{i}", session_id=SR_A,
                                status="draft")
    _stamp_chunk_props(seeded_sdk)
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
    past the reader window (the C4 chunks are pool entries, not a bypass
    of the reader budget)."""
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
            "--coverage-loop", "--session-reinjection",
            "--output", str(tmp_path / "r.json")])
    assert excinfo.value.code == 1
    assert "arm conflict" in capsys.readouterr().err
    assert not (tmp_path / "r.json").exists()


def test_run_level_tristate_and_methodology(monkeypatch, tmp_path):
    """CLI flag > env > OFF at the run level, and the resolved arm + guard
    are recorded in the methodology (methodology == actual)."""
    from tools.longmem_eval import run as _run
    base = ["--data", str(MINI), "--limit", "1", "--split", "s", "--mock",
            "--output", str(tmp_path / "r.json")]
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
