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

from tests import _live_utils
from tortoise.sdk import TortoiseSDK


# ── Live-FalkorDB availability (the FTS backend the expansion needs) ────────
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
    not FALKORDB_AVAILABLE, reason="requires TORTOISE_DB_URI (live FalkorDB FTS lane — tier-2 embedded legs skip)")

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

def test_expansion_still_resolves_a_terminal_object_anchor(seeded_sdk):
    """#3301: C2's anchor resolution is a RESOLUTION path (the anchor Object is
    never surfaced), so a terminal Object anchor must still resolve. Without
    the ``excluded_statuses=()`` opt-out the widened Object exclusion makes
    the anchor unresolvable, the pass returns None, and the cross-session
    join is silently dropped."""
    proj = seeded_sdk._get_proj()
    proj.g.query("MATCH (o:Object {name:$n}) SET o.status='superseded'",
                 params={"n": ENTITY_ANCHOR})
    on_ids = [h["id"] for h in seeded_sdk.tortoise_fts_query(
        QUESTION, limit=20, pool_size=60, entity_key_expansion=True)]
    assert JOIN_ID in on_ids, (
        "a terminal Object anchor must still resolve so the key expansion "
        "reaches the joined cross-session point")


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


# ═══════════════════════════════════════════════════════════════════════════
# (d) #2518 indicator 2 — DENSE-leg parity: the expansion re-embeds
# ═══════════════════════════════════════════════════════════════════════════
#
# The sparse half re-runs the sparse leg with the harvested aliases; before
# this half existed the dense leg kept the ORIGINAL query's vector, so a
# same-subject point whose embedding is close to the ENTITY's vocabulary but
# not to the question's wording stayed invisible to it.
#
# Fixture geometry (a FAKE encoder, so no model ever loads and every distance
# is exact). Two dims carry meaning:
#
#   dim 0 — the "bike" class   (question, seed, and the anchor share it)
#   dim 1 — the "wheel/spoke/tyre/hub" class — a SEMANTIC class the sparse leg
#           cannot traverse: the alias vocabulary says ``wheel``, the other
#           session's point says ``tyre``, and no token matches. Only an
#           embedding bridges that, which is exactly the parity being tested.
#
# So: the seed point's ``search_keys`` (``wheel truing spoke tension``) become
# the composed dense query's only new vocabulary; the session-B point is close
# to THAT composition and far from the plain question; and the sparse OR union
# of ``wheel|truing|spoke|tension`` matches nothing in session B (its content
# says ``tyre``/``hub``). Any appearance of the session-B point is therefore
# attributable to the DENSE pass and to nothing else.

DENSE_SEED_ID = "p2518denseA0"   # session A — the alias carrier
DENSE_JOIN_ID = "p2518denseB1"   # session B — reachable only by the embedding
DENSE_SEED_SESSION = "sessA2518d"
DENSE_JOIN_SESSION = "sessB2518d"
DENSE_SEED_CONTENT = "the road bike repairs cost 120 dollars at the shop"
DENSE_SEED_KEYS = "wheel truing spoke tension"
DENSE_JOIN_CONTENT = (
    "on saturday the mechanic replaced the rear tyre and the hub")
#: The pool/leg window the geometry needs: with str_limit == 2, the plain
#: vector leg returns the seed (distance 0) + one distractor (1.0) and MISSES
#: session B (sqrt 2) — the starved-pool shape in miniature.
_DENSE_POOL = 2


def _semantic_vec(text: str) -> list:
    """The fake encoder's deterministic 384-wide vector (see the section note)."""
    from tortoise.embeddings import EMBEDDING_DIM

    t = (text or "").lower()
    v = [0.0] * EMBEDDING_DIM
    v[0] = 1.0 if "bike" in t else 0.0
    v[1] = 1.0 if any(
        w in t for w in ("wheel", "spoke", "tyre", "hub", "truing")) else 0.0
    v[2] = 0.1
    return v


class _FakeEncoder:
    """Records every encode call; deterministic vectors; optional failure.

    Shaped like the real model (`encode(list[str]) -> list` of objects with
    ``tolist()``) so the SAME double serves the write path
    (``encode_for_row`` → ``compute_embeddings``) and both dense passes.
    """

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail_on = fail_on

    def encode(self, texts, **_kw):
        batch = [str(t) for t in texts]
        self.calls.append(batch)
        if self.fail_on is not None and any(
                self.fail_on in t for t in batch):
            raise RuntimeError("encoder exploded (test double)")
        return [_Vec(_semantic_vec(t)) for t in batch]


class _Vec:
    """Minimal ndarray stand-in — the only member the seams read."""

    def __init__(self, values) -> None:
        self._values = values

    def tolist(self):
        return list(self._values)


@pytest.fixture
def _dense_encoder(monkeypatch):
    """A working fake embedder (overrides the module's ``_no_embedder``).

    BOTH seams are replaced, and both must be: the dense passes read the model
    through ``EmbeddingModel.get()``, while the WRITE path goes through the
    module-global ``compute_embedding`` (:func:`tortoise.embeddings.encode_for_store`
    calls it directly, and the autouse fixture pins it to ``None``). Patching
    only the singleton stores NO vectors and the dense leg is empty — which is
    why the fixture replaces the pair rather than the more obvious half.
    """
    import tortoise.embeddings as _emb

    def _install(fail_on: str | None = None) -> _FakeEncoder:
        enc = _FakeEncoder(fail_on=fail_on)
        monkeypatch.setattr(_emb.EmbeddingModel, "get",
                            staticmethod(lambda: enc))
        monkeypatch.setattr(
            _emb, "compute_embedding",
            staticmethod(
                lambda content, max_tokens=512: _semantic_vec(content)))
        return enc
    return _install


@pytest.fixture
def dense_seeded_sdk(_dense_encoder, monkeypatch):
    """A dedicated graph shaped so the ENTITY alias vocabulary is the only
    bridge to the second session (see the section note).

    The fake encoder is installed BEFORE any point is written: the embeddings
    are produced by the SDK's own write seam (`create_point` →
    `encode_for_row`), so the stored vectors are exactly the ones the real
    write path would store — the fixture never hand-writes a `vecf32` value.
    """
    _dense_encoder()  # the write-path encoder (each test re-installs its own)
    monkeypatch.setenv("TORTOISE_DB_URI", _fresh_uri())
    sdk = TortoiseSDK()
    try:
        sdk.create_entity("object", ENTITY_ANCHOR, objectKind="core:other",
                          is_episodic=True)
        sdk.create_point(
            "statement", DENSE_SEED_CONTENT,
            id=DENSE_SEED_ID, session_id=DENSE_SEED_SESSION,
            search_keys=DENSE_SEED_KEYS, status="draft")
        sdk.create_point(
            "statement", DENSE_JOIN_CONTENT,
            id=DENSE_JOIN_ID, session_id=DENSE_JOIN_SESSION, status="draft")
        for i in range(12):
            sdk.create_point(
                "statement", f"{DISTRACTOR_TOPIC} number {i}",
                id=f"p2518densedist{i}", session_id=f"densedist2518{i}",
                status="draft")
        sdk._get_proj().g.query(
            "MATCH (p:Point), (o:Object {name:$name}) "
            "WHERE p.id IN $ids MERGE (p)-[:aboutObject]->(o)",
            params={"name": ENTITY_ANCHOR,
                    "ids": [DENSE_SEED_ID, DENSE_JOIN_ID]})
        yield sdk
    finally:
        sdk.close()


def _dense_read(sdk, *, trace=None):
    """One read at the fixture's narrow window (the starved-pool shape)."""
    return [
        h["id"] for h in sdk.tortoise_fts_query(
            QUESTION, limit=5, pool_size=_DENSE_POOL,
            entity_key_expansion=True, leg_trace=trace)
    ]


def test_dense_parity_reembeds_and_surfaces_the_other_session(
        dense_seeded_sdk, _dense_encoder, monkeypatch):
    """The mechanism: with the dense switch armed, the expansion re-embeds the
    composed query (exactly ONE extra embed) and session B joins the dense leg
    — a point the sparse expansion provably cannot reach (see the section
    note) and the plain dense leg provably misses (window 2, distance sqrt 2).
    """
    enc = _dense_encoder()
    monkeypatch.setenv("TORTOISE_ENTITY_KEY_DENSE", "1")
    trace: list[dict] = []
    on_ids = _dense_read(dense_seeded_sdk, trace=trace)

    assert DENSE_JOIN_ID in on_ids, (
        "dense-leg parity must surface the session-B point the plain dense "
        "leg missed and the sparse expansion cannot reach")
    # BOUNDED extra embeds: the read's own query, then ONE composed query.
    assert len(enc.calls) == 2, (
        f"a docked read must embed the query once and the composed expansion "
        f"once — got {len(enc.calls)} call(s)")
    composed = enc.calls[1][0]
    assert composed.startswith(QUESTION.strip())
    assert "wheel" in composed, (
        "the composed dense query must carry the harvested alias vocabulary")
    # the composition is bounded (the issue's own budget claim)
    from tortoise.sdk import _ENTITY_DENSE_ALIAS_CHARS
    assert len(composed) <= len(QUESTION) + _ENTITY_DENSE_ALIAS_CHARS + 2
    # Two VECTOR trace entries: the shipped leg's and the expansion's — the
    # evidence that a second dense pass ran. And they are distinguishable by
    # PROVENANCE, not by position: the two entries are otherwise shape-identical
    # (same leg/ran/degraded/reason AND the same mechanism), so a count alone
    # could never attest that THIS arm's leg engaged (review round 3, PR
    # #11459). The shipped leg carries no provenance key at all.
    from tortoise.search_engine import LEG_PROVENANCE_KEY, PROVENANCE_ENTITY_KEY_EXPANSION
    vec_entries = [e for e in trace if e.get("leg") == "vector"]
    assert len(vec_entries) == 2
    assert vec_entries[0].get(LEG_PROVENANCE_KEY) is None, (
        "the shipped dense leg must stay unlabelled — a provenance value there "
        "would relabel every existing dense read")
    assert vec_entries[1].get(LEG_PROVENANCE_KEY) == (
        PROVENANCE_ENTITY_KEY_EXPANSION), (
        "the expansion's entry must name its pass, or the trace cannot say "
        "which of the two identical-shaped entries it was")


def test_vector_provenance_defaults_to_absent(monkeypatch):
    """FAIL VALUE: an unlabelled caller's trace entry gaining a provenance key
    (every existing dense read's trace would change shape for a lever it did
    not arm). Reachable: ``run_vector_query``'s ``provenance=None`` default."""
    import inspect

    from tortoise.search_engine import _trace_entry, run_vector_query

    assert inspect.signature(run_vector_query).parameters["provenance"].default is None
    assert "provenance" not in _trace_entry(
        "vector", ran=True, degraded=False, reason="ok", count=1)
    assert _trace_entry(
        "vector", ran=True, degraded=False, reason="ok", count=1,
        provenance="x")["provenance"] == "x"


def test_dense_switch_off_keeps_the_shipped_path_byte_identical(
        dense_seeded_sdk, _dense_encoder, monkeypatch):
    """The off-switch's contract: OFF (explicit ``0``, and unset) restores the
    shipped path EXACTLY — one embed of the original query, one vector trace
    entry, identical ids in identical order, and the session-B point absent.
    This is the arm the issue's measurement stands on (comment 5821461301),
    so it must not drift when the dense half ships.
    """
    enc = _dense_encoder()
    # Clear the switch first: the shipped path must be provably unchanged on a
    # box where an operator has exported the lever (which .env.example tells
    # them to do) — otherwise this guard reds on a correctly-armed machine and
    # blames a drift that does not exist (review round 2, PR #11459).
    monkeypatch.delenv("TORTOISE_ENTITY_KEY_DENSE", raising=False)
    unset_ids = _dense_read(dense_seeded_sdk)
    assert len(enc.calls) == 1, (
        "OFF must embed ONLY the read's own query — no composed embed at all")
    assert enc.calls[0] == [QUESTION]
    assert DENSE_JOIN_ID not in unset_ids, (
        "with the dense half disarmed the session-B point must stay missed "
        "(the starved-pool shape the sparse-only arm was measured on)")

    monkeypatch.setenv("TORTOISE_ENTITY_KEY_DENSE", "0")
    trace: list[dict] = []
    off_ids = _dense_read(dense_seeded_sdk, trace=trace)
    assert off_ids == unset_ids, (
        "explicit OFF must be byte-identical to unset — the switch may not "
        "change the shipped path in either spelling")
    assert [e.get("leg") for e in trace].count("vector") == 1, (
        "OFF must record ONE vector leg entry, exactly as before this lever")


def test_dense_expansion_env_switch_is_fail_safe_off(monkeypatch):
    """The declared env contract (#4097) for the new switch: unset and garbage
    resolve OFF (a typo never arms a lever nobody measured); only the explicit
    truthy vocabulary arms it; explicit falsy spells OFF."""
    from tortoise.sdk import _entity_key_dense_enabled

    monkeypatch.delenv("TORTOISE_ENTITY_KEY_DENSE", raising=False)
    assert _entity_key_dense_enabled() is False
    monkeypatch.setenv("TORTOISE_ENTITY_KEY_DENSE", "garbage")
    assert _entity_key_dense_enabled() is False
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("TORTOISE_ENTITY_KEY_DENSE", truthy)
        assert _entity_key_dense_enabled() is True, truthy
    for falsy in ("0", "false", "no", "off"):
        monkeypatch.setenv("TORTOISE_ENTITY_KEY_DENSE", falsy)
        assert _entity_key_dense_enabled() is False, falsy


def test_dense_composer_bounds_and_no_op_guard():
    """`_entity_key_dense_query` — the pure half. It must (a) no-op when the
    aliases add no new token (a re-embed that cannot change the vector is pure
    latency), (b) never exceed the char/term budgets, (c) be deterministic and
    order-preserving (same harvest ⇒ same string ⇒ same vector)."""
    from tortoise.sdk import (
        _ENTITY_DENSE_ALIAS_CHARS,
        _ENTITY_DENSE_ALIAS_TERMS,
        _entity_key_dense_query,
    )

    base = QUESTION
    # (a) nothing new -> None (the shipped no-wasted-pass guard, inherited).
    assert _entity_key_dense_query(base, ["road bike repairs"]) is None
    assert _entity_key_dense_query(base, []) is None
    assert _entity_key_dense_query("", ["wheel"]) is None
    assert _entity_key_dense_query(base, ["   "]) is None
    # (b) the char budget holds even for a huge alias pool.
    huge = [f"aliasword{i} padding tokens here" for i in range(200)]
    composed = _entity_key_dense_query(base, huge)
    assert composed is not None
    assert len(composed) <= len(base) + 1 + _ENTITY_DENSE_ALIAS_CHARS
    # (b') the TERM budget holds on its own (many short aliases) — and it
    # bounds the tokens the composition APPENDS, not merely the novel ones
    # (counting only the novel half made the bound unable to bind: a measured
    # composition appended 33 tokens under a 32-token budget — review finding,
    # PR #11459).
    many_short = [f"zq{i}" for i in range(200)]
    composed_terms = _entity_key_dense_query(base, many_short)
    added = composed_terms[len(base):].split()
    assert len(added) <= _ENTITY_DENSE_ALIAS_TERMS
    # The TERM half must also bind ON ITS OWN, so the input has to be aliases
    # that are SHORT (the char budget must not fire first) and mostly REPEATED
    # from the question (so that counting only the NOVEL tokens would never
    # reach the bound). ``road bike cost <i>`` is that shape: ~2 novel tokens per
    # alias against ~5 appended ones, so a novel-only budget appends 60 tokens
    # where the correct one stops at 30 — measured against the pre-fix composer
    # (review round 1 on PR #11459 caught the earlier shape as non-discriminating:
    # long aliases made the CHAR budget bind first and the assertion passed
    # pre- and post-fix alike).
    from tortoise.sparse import tokenize_sparse_query as _tok
    repeating = [f"road bike cost {i}" for i in range(200)]
    composed_repeat = _entity_key_dense_query(base, repeating)
    tail_tokens = _tok(composed_repeat[len(base):], keep_numeric=True)
    assert len(tail_tokens) <= _ENTITY_DENSE_ALIAS_TERMS
    # the discriminating half: a novel-token count would have let ~2x through.
    assert len(tail_tokens) > _ENTITY_DENSE_ALIAS_TERMS // 2, (
        "the fixture must push the term budget to its bound, or reverting "
        "`len(alias_toks)` to `len(new_toks)` would still pass")
    # (c) deterministic + order-preserving.
    pool = ["wheel truing", "spoke tension", "hydraulic bleed"]
    assert (_entity_key_dense_query(base, pool)
            == _entity_key_dense_query(base, list(pool)))
    assert _entity_key_dense_query(base, pool).endswith(pool[-1])


def test_dense_pass_merges_additively_and_never_drops_a_pass1_id(
        dense_seeded_sdk, _dense_encoder):
    """The merge contract, at the pass boundary: expanded results first, then
    every pass-1-only id in its ORIGINAL order, no duplicates. A pass-1 id that
    is not in the graph at all must survive at the tail — that is the additive
    guarantee, stated so that a future 'just replace the leg' refactor reds."""
    enc = _dense_encoder()
    sdk = dense_seeded_sdk
    pass1 = [("pass1-only-sentinel", 0.42),
             (DENSE_SEED_ID, 0.31),
             ("pass1-second-sentinel", 0.11)]
    assert enc.calls == []  # nothing embedded yet
    merged = sdk._entity_key_dense_pass(
        QUESTION, list(pass1),
        aliases=["wheel truing spoke tension"],
        str_limit=_DENSE_POOL,
        excluded_statuses=None,
        leg_trace=None,
    )
    assert merged is not None, "the expansion must run on this fixture"
    assert len(enc.calls) == 1, "exactly ONE extra embed per dense pass"
    ids = [pid for pid, _s in merged]
    assert len(ids) == len(set(ids)), "the merge must not duplicate an id"
    assert DENSE_JOIN_ID in ids
    # both pass-1-only sentinels survive, in their original relative order
    assert ids.index("pass1-only-sentinel") < ids.index("pass1-second-sentinel")
    for sentinel in ("pass1-only-sentinel", "pass1-second-sentinel"):
        assert ids.index(sentinel) > 0, (
            "pass-1-only ids are APPENDED after the expanded run, never "
            "promoted over it")


def test_dense_pass_is_fail_open_on_encode_failure(
        dense_seeded_sdk, _dense_encoder, monkeypatch):
    """Fail-open (the A4 contract): an encoder that explodes on the composed
    query must leave the read byte-identical to the disarmed arm — same ids,
    same order, no exception, and no degraded declaration for a leg the read
    still has."""
    # The OFF baseline must be measured with the switch cleared — an operator
    # who exported the lever would otherwise make this test compare the ARMED
    # read against itself (review round 2, PR #11459).
    monkeypatch.delenv("TORTOISE_ENTITY_KEY_DENSE", raising=False)
    off_ids = _dense_read(dense_seeded_sdk)

    _dense_encoder(fail_on="wheel")  # only the COMPOSED text carries it
    monkeypatch.setenv("TORTOISE_ENTITY_KEY_DENSE", "1")
    trace: list[dict] = []
    on_ids = _dense_read(dense_seeded_sdk, trace=trace)

    assert on_ids == off_ids, (
        "a failed dense expansion must keep the ORIGINAL dense leg — "
        "byte-identical, never an empty or reordered pool")
    assert [e.get("leg") for e in trace].count("vector") == 1, (
        "a failed expansion must not record a vector entry: a leg-local "
        "failure may never declare a healthy read degraded")


def test_dense_pass_stands_down_when_nothing_new_to_add(
        dense_seeded_sdk, _dense_encoder):
    """The no-wasted-pass guard at the pass boundary: an alias pool made only
    of tokens the query already carries returns None WITHOUT embedding."""
    enc = _dense_encoder()
    merged = dense_seeded_sdk._entity_key_dense_pass(
        QUESTION, [(DENSE_SEED_ID, 0.9)],
        aliases=["road bike repairs cost"],
        str_limit=_DENSE_POOL,
        excluded_statuses=None,
        leg_trace=None,
    )
    assert merged is None
    assert enc.calls == [], "no new alias token means no re-embed at all"


def test_dense_pass_second_leg_does_not_double_count_the_dense_slot(
        dense_seeded_sdk, _dense_encoder, monkeypatch):
    """#4489/#8488 composition: ONE dense slot, ONE tally. The expansion's leg
    query must not add a second ``vector`` note to the retrieval tally — the
    trace records both passes, the meter records one leg."""
    import tortoise.retrieval_metering as _rm

    noted: list[str] = []
    monkeypatch.setattr(_rm, "note_leg",
                        lambda leg, **_k: noted.append(leg))
    enc = _dense_encoder()
    out = dense_seeded_sdk._entity_key_dense_pass(
        QUESTION, [(DENSE_SEED_ID, 0.9)],
        aliases=["wheel truing spoke tension"],
        str_limit=_DENSE_POOL,
        excluded_statuses=None,
        leg_trace=None,
    )
    assert enc.calls, "the pass must actually have run"
    assert out is not None
    assert "vector" not in noted, (
        "the expansion's second dense pass must not be counted as a second "
        "dense leg in the retrieval tally")

def test_eval_fingerprint_records_the_dense_half_only_when_armed(
        monkeypatch, tmp_path):
    """The eval A/B is reconstructable (#2518 indicator 2's measurement hook).

    A future measurement has to be able to say WHICH arm produced a number, and
    the checkpoint fingerprint is what refuses an arm-blended resume. So:
    disarmed ⇒ the key is ABSENT (a checkpoint written before this lever keeps
    its byte-identical fingerprint), armed ⇒ stamped ``True``, and an
    armed checkpoint refuses a disarmed resume.
    """
    from tools.longmem_eval import run as runner

    base = dict(reader_model="mock-reader", judge_model="mock-judge",
                ks=(5,), top_k=5, split="s", ingest_mode="deterministic",
                extractor_model=None, max_retries=3,
                dataset_fingerprint="x", rerank_config={})
    monkeypatch.delenv("TORTOISE_ENTITY_KEY_DENSE", raising=False)
    fp_off = runner._build_fingerprint(**base)
    assert "entity_key_dense" not in fp_off, (
        "the conditional-presence convention: a disarmed run must not stamp an "
        "inert key into the fingerprint, or every existing checkpoint refuses")
    assert fp_off == runner._build_fingerprint(**base, entity_key_dense=None)

    # An ARMED env var with the sparse lever OFF cannot make the read run the
    # dense half (the pass is a parity), so the call site stamps the arm only
    # when BOTH levers are on — otherwise the record asserts an arm that
    # provably did not run, and an equivalent resume is refused over an inert
    # env var (review finding, PR #11459).
    from tortoise.sdk import _entity_key_dense_enabled
    assert _entity_key_dense_enabled() is False  # unset in this process
    assert runner._resolved_dense_arm(False) is None
    assert runner._resolved_dense_arm(None) is None
    assert runner._resolved_dense_arm(True) is None  # env unset here
    monkeypatch.setenv("TORTOISE_ENTITY_KEY_DENSE", "1")
    assert runner._resolved_dense_arm(True) is True
    assert runner._resolved_dense_arm(False) is None
    monkeypatch.delenv("TORTOISE_ENTITY_KEY_DENSE", raising=False)

    fp_on = runner._build_fingerprint(**base, entity_key_dense=True)
    assert fp_on["entity_key_dense"] is True
    # The REPORT's methodology block must carry the same value, or a published
    # number from a dense-armed run is indistinguishable from the measured
    # sparse-only arm's (review round 2, PR #11459). `r1_knobs` is built inline
    # inside `run_evaluation` and has no seam, so the guard is a source pin: it
    # reds if the line is dropped or a second value is invented.
    import inspect

    src = inspect.getsource(runner.run_evaluation)
    assert '"entity_key_dense": _resolved_dense_arm(' in src, (
        "the methodology must record the dense arm through the ONE resolver — "
        "otherwise a report cannot name the arm it measured")
    cp = tmp_path / "cp-dense.json"
    runner._save_checkpoint(str(cp), [], [], fp_on)
    with pytest.raises(runner.CheckpointStaleError):
        runner._load_checkpoint(str(cp), fp_off)


def test_dense_expansion_cannot_launder_a_hollow_scope_into_hybrid(
        dense_seeded_sdk, _dense_encoder, monkeypatch):
    """#4199/#5287 regression (found in review, not shipped): the expansion's
    second dense leg must measure the READ's own kind scope.

    A traced, kind-scoped read whose scope has nodes but ZERO embeddings
    declares ``no_embeddings``. An expansion leg that ran UNSCOPED recorded a
    healthy entry instead, and ONE healthy vector entry is all
    ``declared_degraded_read`` needs to call the read hybrid — so the expansion
    silently laundered a genuine degradation into ``hybrid: True``. The pass now
    forwards ``scope_kinds``, so its entry is re-labelled exactly like the
    shipped leg's and the declaration is unchanged.
    """
    import tortoise.embeddings as _emb
    from tortoise.search_engine import declared_degraded_read

    _dense_encoder()
    # Unembedded POINTS of the read's scope: "decision" nodes exist, none has a
    # vector — the hollow-scope shape #4199 is about.
    monkeypatch.setattr(
        _emb, "compute_embedding",
        staticmethod(lambda content, max_tokens=512: (
            None if "unembedded" in str(content)
            else _semantic_vec(content))))
    sdk = dense_seeded_sdk
    for i in range(3):
        sdk.create_point("decision", f"unembedded decision number {i}",
                         id=f"p2518hollow{i}", session_id=f"hollow2518{i}",
                         status="draft")

    def _declaration() -> dict | None:
        trace: list[dict] = []
        sdk.tortoise_fts_query(
            QUESTION, limit=5, pool_size=_DENSE_POOL, kind="decision",
            entity_key_expansion=True, leg_trace=trace)
        return declared_degraded_read(trace), trace

    monkeypatch.delenv("TORTOISE_ENTITY_KEY_DENSE", raising=False)
    off_decl, _off_trace = _declaration()
    assert off_decl is not None and off_decl["reason"] == "no_embeddings", (
        "the fixture must produce the hollow-scope declaration at all — "
        f"otherwise this test proves nothing (got {off_decl})")

    monkeypatch.setenv("TORTOISE_ENTITY_KEY_DENSE", "1")
    on_decl, on_trace = _declaration()
    assert [e.get("leg") for e in on_trace].count("vector") == 2, (
        "the expansion's second dense leg must actually have run, or the "
        "laundering path is not exercised")
    assert on_decl is not None and on_decl["reason"] == "no_embeddings", (
        "the expansion's unscoped healthy entry laundered the read's genuine "
        f"degradation into hybrid (got {on_decl})")
