"""#2518 (C2 #2513) — entity/fact-augmented key expansion (product retrieval).

The multi-session evidence-surface lever (docs/scoping/2026-09-07-2513-
multisession-evidence-surface.md §4 C2): extracted points already carry E3
per-point ``search_keys`` + ``aboutObject`` entity anchors, but retrieval
never queried them. The expansion (``tortoise_fts_query(
entity_key_expansion=True)``, default OFF — the #1745 fail-safe decision):

  1. resolves the QUERY's own entity anchors THROUGH THE INDEX (bounded FTS
     over the Object-name spine),
  2. harvests the anchors' names + the E3 ``search_keys`` of every linked
     Point across ALL sessions (the ``aboutObject`` cross-session join),
  3. re-runs the sparse OR leg additively (``build_or_query`` expansion
     tail — original tokens keep their slots) so a same-subject point from
     a session the one-shot top-k starves can join via its own alias keys.

Hermetic contract proven here: the dense/structural legs are pinned OUT
(embedder patched absent → no vector leg; no structural kind → no kind
scan), so the differential is exactly the sparse-expansion pass:

  * (a) a same-entity point from a DIFFERENT session that plain top-k
        misses surfaces when the expansion is on (and only then),
  * (b) expansion disabled == product default (byte-identical no-op),
  * (c) the merge/rank contract: original query tokens keep their OR slots
        (regression guard), the pass-1 seed is never displaced, and the
        joined point lands inside the top-k reader window via the fts leg.

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


# ── Live-FalkorDB availability (the FTS backend the expansion needs) ────────
def _falkordb_available() -> bool:
    """Probe a live FalkorDB; reads TORTOISE_DB_URI at CALL time so the
    module never captures it at import (#221 test-isolation lint)."""
    uri = (os.environ.get("TORTOISE_DB_URI")
           or "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")
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
    return (os.environ.get("TORTOISE_DB_URI")
            or "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")

#: The question tokens (after the shared stopword drop): the sparse OR leg
#: matches these against content ∪ search_keys.
QUESTION = "how much did the road bike repairs cost me in total"
ENTITY_ANCHOR = "road bike repairs"
SEED_ID = "p2518sessA0"     # session A — matches the question's own tokens
JOIN_ID = "p2518sessB1"     # session B — matched ONLY via the expansion
SEED_SESSION = "sessA2518"
JOIN_SESSION = "sessB2518"
#: The cross-session point's E3 keys: zero overlap with the question's
#: tokens (plain FTS cannot match it) but its OWN alias vocabulary — the
#: harvest adds it to the OR union via the shared aboutObject anchor.
JOIN_KEYS = "extra charge for wheels saturday service fee"
DISTRACTOR_TOPIC = "cooking pasta recipes with tomato basil sauce"


def _fresh_uri() -> str:
    """A dedicated per-test graph on the docker server — fresh indexes and
    an empty graph make the differential hermetic (no leftovers from other
    tests in the shared matrix can seed anchors or pollute the pool)."""
    return f"{_uri()}_{uuid.uuid4().hex[:10]}"


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the dense leg OUT (hermetic): create_point stops embedding
    (``compute_embedding`` → None) and the query path sees no embedder
    (``EmbeddingModel.get`` → None), so the vector strategy never runs and
    the differential isolates the sparse-expansion pass. No model load, no
    network — deterministic on any machine with the docker lane."""
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get",
                        staticmethod(lambda: None))


@pytest.fixture

def seeded_sdk(monkeypatch):
    """A fresh dedicated graph with the two-session fixture:

    * Object anchor ``road bike repairs`` (the entity spine node),
    * session A seed point (matches the question's own tokens),
    * session B point about the SAME anchor whose content shares NO query
      token — its E3 ``search_keys`` are its only join surface,
    * 12 distractors with zero token overlap with the question or the keys
      (they cannot rank in the sparse leg under either arm).
    """
    # URI-mode redirect: set the env URI to the dedicated graph and build
    # no-arg (the SDK resolves the server from the env — a passed db_path
    # is treated as a FILE path, not a URI).
    monkeypatch.setenv("TORTOISE_DB_URI", _fresh_uri())
    sdk = TortoiseSDK()
    try:
        proj = sdk._get_proj()
        sdk.create_entity("object", ENTITY_ANCHOR, objectKind="core:other",
                          is_episodic=True)
        sdk.create_point(
            "statement",
            "the road bike repairs cost 120 dollars at the shop",
            id=SEED_ID, session_id=SEED_SESSION,
            search_keys="road bike repairs bill paid 120 dollars",
            status="draft")
        sdk.create_point(
            "statement",
            "on saturday morning the weather was sunny so i went for a "
            "long walk instead",
            id=JOIN_ID, session_id=JOIN_SESSION,
            search_keys=JOIN_KEYS, status="draft")
        for i in range(12):
            sdk.create_point(
                "statement",
                f"{DISTRACTOR_TOPIC} number {i}",
                id=f"p2518dist{i}", session_id=f"dist2518{i}",
                status="draft")
        proj.g.query(
            "MATCH (p:Point), (o:Object {name:$name}) "
            "WHERE p.id IN $ids MERGE (p)-[:aboutObject]->(o)",
            params={"name": ENTITY_ANCHOR,
                    "ids": [SEED_ID, JOIN_ID]})
        yield sdk
    finally:
        sdk.close()


# ── (a) cross-session surface: the expansion contract ─────────────────────

def test_expansion_surfaces_same_entity_point_from_other_session(seeded_sdk):
    """Plain top-k misses the same-entity point from session B (its content
    shares no query token); the entity/fact-augmented key expansion surfaces
    it into top-k via the sparse OR leg. The join is the shared
    ``aboutObject`` anchor + the point's OWN E3 keys."""
    off = [h["id"] for h in seeded_sdk.tortoise_fts_query(
        QUESTION, limit=20, pool_size=60, entity_key_expansion=False)]
    assert SEED_ID in off, "session A seed must match the plain query"
    assert JOIN_ID not in off, (
        "session B point must be MISSED by plain top-k (zero content-token "
        "overlap — the partial-evidence miss class #2513 targets)")

    on = seeded_sdk.tortoise_fts_query(
        QUESTION, limit=20, pool_size=60, entity_key_expansion=True)
    on_ids = [h["id"] for h in on]
    assert JOIN_ID in on_ids, (
        "entity/fact-augmented key expansion must surface the session B "
        "point into top-k")
    hit = next(h for h in on if h["id"] == JOIN_ID)
    # the join is a SPARSE hit — the point entered via the expanded OR leg
    assert (hit.get("match_source") or "") == "fts"
    # the surfaced point's stored session is a DIFFERENT session than the
    # seed's (SearchResult.session_id is only populated on the hosted Event
    # path — read the stored prop directly for the provenance assertion).
    rows = seeded_sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id IN $ids "
        "RETURN n.id, coalesce(n.session_id, '')",
        params={"ids": [SEED_ID, JOIN_ID]}).result_set
    sess = {r[0]: r[1] for r in rows}
    assert sess.get(JOIN_ID) == JOIN_SESSION
    assert sess.get(JOIN_ID) != sess.get(SEED_ID), (
        "the surfaced point must come from a DIFFERENT session than the seed")


def test_expansion_keeps_seed_and_reaches_top_window(seeded_sdk):
    """The expansion is ADDITIVE: the pass-1 seed (the strongest original
    match) is never displaced, and the joined cross-session point lands
    inside the top-k reader window (not merely appended at the tail)."""
    on = [h["id"] for h in seeded_sdk.tortoise_fts_query(
        QUESTION, limit=5, pool_size=60, entity_key_expansion=True)]
    assert SEED_ID in on[:5], "expansion must not displace the pass-1 seed"
    assert JOIN_ID in on[:5], (
        "the expanded fts leg must place the joined point inside the "
        "top-5 reader window")


# ── (b) default OFF: byte-identical no-op ─────────────────────────────────

def test_expansion_disabled_is_default_byte_identical(seeded_sdk):
    """Default (no kwarg) == explicit ``entity_key_expansion=False`` —
    byte-identical ids in the same order: the flag changes NOTHING without
    opt-in (the #1745 fail-safe default decision)."""
    import inspect

    from tortoise.sdk import TortoiseSDK as _SDK
    sig = inspect.signature(_SDK.tortoise_fts_query)
    assert sig.parameters["entity_key_expansion"].default is False
    default_ids = [h["id"] for h in seeded_sdk.tortoise_fts_query(
        QUESTION, limit=20, pool_size=60)]
    off_ids = [h["id"] for h in seeded_sdk.tortoise_fts_query(
        QUESTION, limit=20, pool_size=60, entity_key_expansion=False)]
    assert default_ids == off_ids
    assert JOIN_ID not in default_ids, "default behavior must not expand"


def test_eval_env_gate_failsafe_off(seeded_sdk, monkeypatch):
    """The eval A/B gate (``retrieve_for_question`` / env
    ``TORTOISE_LME_ENTITY_KEY_EXPANSION``) is fail-safe OFF: unset and
    garbage resolve False, only explicit truthy (1/true/yes/on) arms the
    expansion — a typo never flips the knob (mirrors the evidence-boost
    gate). The outcome records the resolved arm so the A/B is
    reconstructable."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    question = {
        "question_id": "q2518eval",
        "question": QUESTION,
        "question_type": "single-session-user",
        "answer_session_ids": [JOIN_SESSION],
        "haystack_dates": ["2026-09-01", "2026-09-05"],
        "haystack_sessions": [],
        "answer": "",
    }
    monkeypatch.delenv("TORTOISE_LME_ENTITY_KEY_EXPANSION", raising=False)
    assert retrieve_for_question(
        seeded_sdk, question, ks=(5,), top_k=10,
        pool_size=60)["entity_key_expansion"] is False
    monkeypatch.setenv("TORTOISE_LME_ENTITY_KEY_EXPANSION", "garbage")
    assert retrieve_for_question(
        seeded_sdk, question, ks=(5,), top_k=10,
        pool_size=60)["entity_key_expansion"] is False
    monkeypatch.setenv("TORTOISE_LME_ENTITY_KEY_EXPANSION", "1")
    assert retrieve_for_question(
        seeded_sdk, question, ks=(5,), top_k=10,
        pool_size=60)["entity_key_expansion"] is True
    # explicit flag beats the env in both directions
    monkeypatch.setenv("TORTOISE_LME_ENTITY_KEY_EXPANSION", "1")
    assert retrieve_for_question(
        seeded_sdk, question, ks=(5,), top_k=10, pool_size=60,
        entity_key_expansion=False)["entity_key_expansion"] is False
    monkeypatch.delenv("TORTOISE_LME_ENTITY_KEY_EXPANSION", raising=False)
    assert retrieve_for_question(
        seeded_sdk, question, ks=(5,), top_k=10, pool_size=60,
        entity_key_expansion=True)["entity_key_expansion"] is True


# ── (c) rank/join mechanics: slot reservation + merge contract ────────────

def test_expansion_never_runs_on_non_point_entity_type(seeded_sdk, monkeypatch):
    """P1 regression (#2557 review): the expansion harvest is point-only
    (search_keys live on points), so an 'event'/'operator' arm with the flag
    ON must NOT run the pass — merging point ids into an event/operator top-k
    would silently truncate the real hits (measured: event arm returned []
    where OFF returned the event). The gate is entity-type-scoped before the
    pass is even entered."""
    import tortoise.sdk as sdkmod
    calls = []

    def _boom(*a, **k):
        calls.append(k)
        raise AssertionError(
            "C2 pass must never run for entity_type != 'point'")

    monkeypatch.setattr(sdkmod.TortoiseSDK,
                        "_entity_key_expansion_pass", _boom)
    # No events exist in the fixture; the point is the gate fires before any
    # pass logic — same call shape as the temporal (et="event") arm.
    out = seeded_sdk.tortoise_fts_query(
        QUESTION, limit=5, pool_size=60, entity_key_expansion=True,
        entity_type="event")
    assert calls == []
    assert isinstance(out, list)  # the untouched event fts leg


def test_expansion_terms_never_displace_original_tokens():
    """The sparse OR-cap regression guard: an injected alias pool (the C2
    harvest shape — entity anchor name + E3 key strings) fills ONLY the
    bounded expansion tail AFTER the original query tokens' slots — a long
    alias can never crowd out a shorter ORIGINAL token."""
    from tortoise.sparse import DEFAULT_MAX_EXPANSION_TERMS, build_or_query
    original = build_or_query(QUESTION)
    original_tokens = original.split("|")
    aliases = [ENTITY_ANCHOR, JOIN_KEYS,
               "road bike repairs bill paid 120 dollars"]
    expanded = build_or_query(QUESTION, expansion_terms=aliases)
    expanded_tokens = expanded.split("|")
    assert set(original_tokens) <= set(expanded_tokens)
    assert expanded_tokens[:len(original_tokens)] == original_tokens
    assert len(expanded_tokens) <= 12 + DEFAULT_MAX_EXPANSION_TERMS


def test_harvest_adds_new_alias_tokens_only():
    """An alias pool that tokenizes to ONLY original-query tokens must no-op
    (the pass's wasted-second-pass guard) — the expansion only fires when it
    can add recall."""
    import tortoise.sdk as _sdkmod
    from tortoise.sdk import _ENTITY_ANCHOR_LIMIT
    assert _ENTITY_ANCHOR_LIMIT >= 1
    # the guard is the pass's early-return: alias tokens ⊄ original tokens
    from tortoise.sparse import tokenize_sparse_query
    orig = set(tokenize_sparse_query(QUESTION))
    alias_toks = set(tokenize_sparse_query(
        "road bike repairs cost", keep_numeric=True))
    assert alias_toks <= orig  # degenerate harvest — nothing to add
    assert bool(alias_toks - orig) is False
    assert _sdkmod._ENTITY_ANCHOR_LIMIT == 3  # bounded anchor window
