"""#2520 (C6) — the eval-lane time-aware arm (docker lane).

The three-part fix of the reproduced defect (a superseded fact surfacing as
current) has one eval-lane half:

  * the query-side date anchor threads ``time_aware``/``query_date`` into
    the DENSE leg via ``hybrid_search`` (SDK-side contract proven in
    ``tests/test_time_aware_sdk_2520.py``), and
  * ``retrieve_for_question`` applies :func:`tortoise.time_aware.
    prefer_latest_order` to the **final pool, immediately before
    ``_recall_metrics`` reads it** — the LAST order-owner of the measured
    surface, so no later pool-mover (C3-1/C4 merge, C2 boost, R6 rerank)
    can discard it.

Hermetic contract proven here (default OFF; no LLM, no network; dense leg
pinned out):

  * (a) **placement** — with a deterministically STALE-FIRST intermediate
        pool (``hybrid_search`` stubbed, so the differential is pure order),
        the arm reorders the FINAL measured pool live-before-stale while the
        OFF path leaves it untouched; membership is preserved
        (never-starve),
  * (b) **reachability** — on a REAL supersession pair the arm sees the
        stale entry (`supersede_point` writes ``superseded_by`` + a closed
        window + a terminal status) and reports the census,
  * (c) the eval env gate is fail-safe OFF (unset/garbage → OFF; only
        1/true/yes/on arms; the explicit flag beats the env),
  * (d) a TR question is excluded (``tr_excluded`` recorded, ``applied``
        False, and the pool order is identical ON vs OFF — no regression).

Runs against a live FalkorDB (docker lane) on a DEDICATED per-test graph
(fresh indexes, zero cross-test contamination). Requires the docker lane's
FTS backend — skips when unavailable (embedded has no fulltext index).
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tortoise.sdk import TortoiseSDK


# ── Live-FalkorDB availability (the FTS backend the arm needs) ─────────
def _falkordb_available() -> bool:
    """Probe a live FalkorDB; reads TORTOISE_DB_URI at CALL time so the
    module never captures it at import (#221 test-isolation lint)."""
    uri = os.environ.get(
        "TORTOISE_DB_URI",
        "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")
    old = os.environ.get("TORTOISE_DB_URI")
    try:
        os.environ["TORTOISE_DB_URI"] = f"{uri}_probe2520eval"
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

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE,
    reason="requires TORTOISE_DB_URI (live FalkorDB FTS lane)")


def _uri() -> str:
    return os.environ.get(
        "TORTOISE_DB_URI",
        "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")


#: A prefer-latest question: no year, a current-time deictic ("now").
QUESTION = "where do i live now"
QUESTION_DATE = "2026-09-25"
#: The superseded predecessor and its live successor.
STALE_ID = "p2520madrid0"
LIVE_ID = "p2520lisbon0"
STALE_SESSION = "sess2520madrid"
LIVE_SESSION = "sess2520lisbon"


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the dense leg OUT (hermetic): create_point stops embedding and
    the query path sees no embedder, so the differential isolates ordering
    on the sparse + structural legs. No model load, no network."""
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get",
                        staticmethod(lambda: None))


def _question(*, question: str = QUESTION, qtype: str = "knowledge-update",
              qid: str = "q2520test") -> dict:
    return {
        "question_id": qid,
        "question": question,
        "question_type": qtype,
        "answer_session_ids": [LIVE_SESSION],
        "haystack_session_ids": [STALE_SESSION, LIVE_SESSION],
        "haystack_dates": ["2024-03-01", QUESTION_DATE],
        "haystack_sessions": [],
        "answer": "",
        "question_date": QUESTION_DATE,
    }


@pytest.fixture
def seeded_sdk(monkeypatch):
    """A fresh dedicated graph with a REAL supersession pair.

    ``supersede_point`` stamps the predecessor exactly as the product write
    path does — status terminal, a ``CORRECTS`` edge (``superseded_by``) and
    the closed bi-temporal window (``validTo``/``expiredAt``) — so all three
    stale clauses of :func:`tortoise.time_aware.is_stale_entry` are
    reachable from the real write path, not hand-stamped prose.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", f"{_uri()}_"
                       f"{uuid.uuid4().hex[:10]}")
    monkeypatch.delenv("TORTOISE_LME_TIME_AWARE_QE", raising=False)
    sdk = TortoiseSDK()
    try:
        sdk.create_point(
            "statement", "i live in madrid",
            id=STALE_ID, session_id=STALE_SESSION, lme_session_index=0,
            search_keys="home city residence address", status="draft")
        sdk.create_point(
            "statement", "i live in lisbon",
            id=LIVE_ID, session_id=LIVE_SESSION, lme_session_index=1,
            search_keys="home city residence address", status="draft")
        sdk.supersede_point(STALE_ID, LIVE_ID, valid_from="2026-01-01")
        yield sdk
    finally:
        sdk.close()


def _stale_first_hybrid(monkeypatch):
    """Stub ``hybrid_search`` to a DETERMINISTIC stale-first pool.

    The real fusion ranks the CORRECTS successor up (its edge expands the
    structural leg), so a raw-pool inversion is not reliably constructible
    from fixture data — and the arm's own recency weight also pushes the
    NEWER-created row up, which is not necessarily the live one. Stubbing
    makes the placement differential pure order — the only variable is the
    arm. Stale-first is expressed via the ``superseded_by`` payload key, the
    same surface ``_annotate_hits`` copies through to the measured pool.

    Returns a list the stub appends each call's kwargs to, so the arm's
    threading (``time_aware``/``query_date``/``recency_boost``) is
    observable without the embedder.
    """
    import tools.longmem_eval.retrieve as _r
    stale = {"id": STALE_ID, "content": "i live in madrid",
             "match_source": "fts",
             "superseded_by": {"id": LIVE_ID}}
    live = {"id": LIVE_ID, "content": "i live in lisbon",
            "match_source": "fts"}
    calls: list[dict] = []

    def _fake(sdk, query, **kw):
        calls.append(kw)
        return [dict(stale), dict(live)]

    monkeypatch.setattr(_r, "hybrid_search", _fake)
    return calls


def _ids(pool: list[dict]) -> list[str]:
    return [h["id"] for h in pool]


# ── (a) placement: the FINAL pool is reordered ───────────────────────────

def test_arm_reorders_the_final_pool(seeded_sdk, monkeypatch):
    """FAIL VALUE: the stale entry left ahead of the live one in the
    MEASURED pool (``ret["hits"]``) when the arm is ON, or the OFF path
    reordering a stale-first pool. Reachable: ``hybrid_search`` is stubbed
    stale-first, so the intermediate order is known exactly."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    _stale_first_hybrid(monkeypatch)
    q = _question()
    off = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60, time_aware_qe=False)
    assert _ids(off["hits"]) == [STALE_ID, LIVE_ID], (
        "the OFF path must not touch the pool order")
    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60, time_aware_qe=True)
    ids = _ids(on["hits"])
    assert ids.index(LIVE_ID) < ids.index(STALE_ID), (
        "the live version must precede the superseded one in the measured "
        "pool")
    stats = on["time_aware_stats"]
    assert stats["applied"] is True, stats
    assert stats["reason"] == "latest-first", stats
    assert stats["live"] == 1 and stats["stale"] == 1, stats
    assert stats["intent"] == "prefer-latest", stats
    assert stats["tr_excluded"] is False, stats
    assert set(ids) == {STALE_ID, LIVE_ID}, (
        "prefer_latest_order must never change pool membership")


def test_off_path_has_no_arm_keys_and_matches_explicit_false(
        seeded_sdk, monkeypatch):
    """FAIL VALUE: an arm key (`time_aware_stats`) or an order change on the
    default OFF path. Reachable: ``time_aware_qe`` defaults to None and the
    env is unset by the fixture."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    _stale_first_hybrid(monkeypatch)
    q = _question()
    default = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                    pool_size=60)
    explicit = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                     pool_size=60, time_aware_qe=False)
    assert default["time_aware_qe"] is False
    assert "time_aware_stats" not in default, (
        "the off path must keep today's exact outcome shape (D2)")
    assert _ids(default["hits"]) == _ids(explicit["hits"])


# ── (b) reachability: the REAL supersession state reaches the arm ────────

def test_real_supersession_census_reaches_the_arm(seeded_sdk):
    """FAIL VALUE: a ``time_aware_stats`` census that does not see the stale
    entry (the stale clause never reaching the integration point).
    Reachable: ``supersede_point`` stamps ``superseded_by`` + a closed
    window + a terminal status on the predecessor."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    on = retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                               pool_size=60, time_aware_qe=True)
    ids = _ids(on["hits"])
    assert STALE_ID in ids and LIVE_ID in ids, (
        "fixture reachability: BOTH the superseded and the live point must "
        "be in the pool")
    stats = on["time_aware_stats"]
    assert stats["stale"] >= 1, (
        "the superseded entry must be seen as stale from the REAL write "
        "path's state")
    assert stats["intent"] == "prefer-latest", stats
    assert stats["tr_excluded"] is False, stats


def test_status_only_stale_row_is_demoted(seeded_sdk, monkeypatch):
    """FAIL VALUE: a `retract_point`-shaped row (status='retracted', no
    CORRECTS edge, no window) NOT demoted — the case that only the annotated
    `status` key catches. Reachable: ``hybrid_search`` is stubbed to return
    exactly that payload, so a missing/renamed `status` key leaves the row
    live and the reorder a no-op."""
    import tools.longmem_eval.retrieve as _r
    from tools.longmem_eval.retrieve import retrieve_for_question
    stale = {"id": STALE_ID, "content": "i live in madrid",
             "match_source": "fts", "status": "retracted"}
    live = {"id": LIVE_ID, "content": "i live in lisbon",
            "match_source": "fts"}
    monkeypatch.setattr(_r, "hybrid_search",
                        lambda sdk, query, **kw: [dict(stale), dict(live)])
    on = retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                               pool_size=60, time_aware_qe=True)
    assert on["time_aware_stats"]["stale"] == 1, on["time_aware_stats"]
    ids = _ids(on["hits"])
    assert ids.index(LIVE_ID) < ids.index(STALE_ID), (
        "the status-only stale row must be reordered behind the live one")
    # And the status must ride the measured annotated surface by name.
    stale_hit = next(h for h in on["hits"] if h["id"] == STALE_ID)
    assert stale_hit["status"] == "retracted"


# ── (c) the env gate is fail-safe OFF ─────────────────────────────────────

@pytest.mark.parametrize("env_value, expected", [
    (None, False), ("", False), ("garbage", False), ("0", False),
    ("1", True), ("true", True), ("YES", True), ("on", True),
])
def test_env_gate_fail_safe(seeded_sdk, monkeypatch, env_value, expected):
    """FAIL VALUE: anything other than 1/true/yes/on arming the read surface.
    Reachable: the resolver reads the env at call time."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    if env_value is None:
        monkeypatch.delenv("TORTOISE_LME_TIME_AWARE_QE", raising=False)
    else:
        monkeypatch.setenv("TORTOISE_LME_TIME_AWARE_QE", env_value)
    out = retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                               pool_size=60)
    assert out["time_aware_qe"] is expected, env_value


def test_explicit_flag_beats_env(seeded_sdk, monkeypatch):
    """FAIL VALUE: an explicit False overridden by an arming env (or vice
    versa). Reachable: the tri-state's explicit branch."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    monkeypatch.setenv("TORTOISE_LME_TIME_AWARE_QE", "1")
    off = retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                                pool_size=60, time_aware_qe=False)
    assert off["time_aware_qe"] is False
    assert "time_aware_stats" not in off
    monkeypatch.setenv("TORTOISE_LME_TIME_AWARE_QE", "garbage")
    on = retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                               pool_size=60, time_aware_qe=True)
    assert on["time_aware_qe"] is True
    assert "time_aware_stats" in on


# ── (d) TR is excluded, no regression ─────────────────────────────────────

def test_tr_question_is_excluded_and_order_unchanged(
        seeded_sdk, monkeypatch):
    """FAIL VALUE: the arm reordering a TR question's pool (the R5 stack must
    stay in charge) or omitting the exclusion marker. Reachable: a TR
    question_type routes ``tr_excluded=True``; the stub is stale-first so an
    accidental reorder is observable."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    _stale_first_hybrid(monkeypatch)
    q = _question(question="what did i do in 2024", qtype="temporal-reasoning",
                  qid="q2520tr")
    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60, time_aware_qe=True)
    assert on["time_aware_qe"] is True
    assert on["time_aware_stats"]["tr_excluded"] is True
    assert on["time_aware_stats"]["applied"] is False
    assert _ids(on["hits"]) == [STALE_ID, LIVE_ID], (
        "a TR question's pool order must be untouched by the time-aware arm")


# ── the intent is query-text driven, not label-gated ──────────────────────

# ── the arm's threading into the retrieval call (D3/D6) ──────────────────

def test_arm_threads_anchor_and_intent_gated_recency(
        seeded_sdk, monkeypatch):
    """FAIL VALUE: the dense-anchor kwargs (`time_aware`/`query_date`)
    threaded on the OFF path, or the recency weight applied for a
    DATE-PINNED / no-intent question (the invert-recency guard). Reachable:
    ``hybrid_search`` is stubbed and records its kwargs."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    calls = _stale_first_hybrid(monkeypatch)
    # OFF: the arm kwargs are absent (byte-identical off path) and the
    # recency weight is off.
    retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                          pool_size=60, time_aware_qe=False)
    assert "time_aware" not in calls[-1]
    assert calls[-1]["recency_boost"] == 0.0
    # ON + prefer-latest: anchored, and the fixed product weight applies.
    retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                          pool_size=60, time_aware_qe=True)
    assert calls[-1]["time_aware"] is True
    assert calls[-1]["query_date"] == QUESTION_DATE
    assert calls[-1]["recency_boost"] == 0.5  # DEFAULT_TIME_AWARE_*
    # The weight seam (D6, test-only) overrides the constant.
    retrieve_for_question(seeded_sdk, _question(), ks=(5,), top_k=10,
                          pool_size=60, time_aware_qe=True,
                          time_aware_recency_weight=0.0)
    assert calls[-1]["recency_boost"] == 0.0
    # ON + date-pinned: the anchor is still threaded (harmless — the intent
    # gate embeds the BARE query) but the recency weight is NOT applied.
    retrieve_for_question(
        seeded_sdk, _question(question="where did i live in 2024"),
        ks=(5,), top_k=10, pool_size=60, time_aware_qe=True)
    assert calls[-1]["time_aware"] is True
    assert calls[-1]["recency_boost"] == 0.0
    assert calls[-1]["recency_fields"] is None


def test_intent_is_label_independent(seeded_sdk):
    """FAIL VALUE: the arm firing only when the label is TR (the reproduced
    cause (b)). Reachable: the SAME non-TR question, arm ON, must resolve
    the freshness intent."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    on = retrieve_for_question(seeded_sdk, _question(qtype="knowledge-update"),
                               ks=(5,), top_k=10, pool_size=60,
                               time_aware_qe=True)
    assert on["time_aware_stats"]["intent"] == "prefer-latest", (
        "a non-TR 'now' question must still resolve the freshness intent")
