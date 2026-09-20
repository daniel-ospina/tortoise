"""#4194 — a captured turn Point must carry the embedding its dense leg searches.

The measured defect (read-only census on a copy of the real store: 55 Points,
18 embedded; EPISODIC TURN Points 27, embedded 0/27): the capture write paths
wrote turn Points with raw Cypher and NO ``embedding``, while ``create_point``
DID embed. So the dense/vector leg had no material for captured turns, the
ranking over them was keyword-only — and the read path still paid to encode a
query vector on every call (~37.8 ms p50). This file pins the write path now
stores a vector, and the two failure modes that matter:

* **The trap** — the stored vector MUST agree with the query encoder in MODEL,
  DIMENSION and NORMALISATION. A mismatched vector still "runs" and returns
  garbage, which is WORSE than the previous honest zero because it looks like it
  works. ``test_..._matches_the_query_encoder`` compares the stored vector
  against the exact call the read path makes; the dimension mutation REDs there
  (proof recorded on PR #4202/#4194).
* **Degradation stays visible** — with no embedder the turn is still stored and
  the read path DECLARES its vector leg not-run (``no_embedder``), never
  ``available``. The declaration is the shared #2952 vocabulary in
  ``tortoise.search_engine.declared_degraded_read`` — no term is minted here.
* **The hosted path is the same write** — ``hosted_api._capture_session_impl``
  duplicates the SDK loop, so it is exercised too.

Embeddings are a LOCAL model (measured: BAAI/bge-small-on-CPU). These tests
stub ``EmbeddingModel.get`` with a deterministic hash encoder — the same
singleton both the write and the query path resolve — so no model, no network
and no LLM runs anywhere.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

# Imported for the hosted parity test only. The heavy module-level import is the
# established pattern for hosted-surface tests (see
# tests/test_capture_loop_responsiveness.py); the offline MockModel seam means
# no provider/network call.
from tests.test_hosted_api import TEST_ORG_ID
from tests.test_hosted_api import client as client
from tortoise.embeddings import EmbeddingModel, compute_embedding
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import run_vector_query


class _FakeEmbedder:
    """Deterministic 384-dim encoder — a healthy embedder with no HF load.

    Distinct texts get distinct vectors (the hash), the same text gets the same
    vector, and the vectors are NOT unit-norm — so the equality assertion below
    also catches a one-sided normalisation change.
    """

    DIM = 384

    def encode(self, texts, batch_size=32, show_progress_bar=False):
        rows = []
        for text in texts:
            digest = hashlib.sha256(str(text).encode("utf-8")).digest()
            raw = (digest * (self.DIM // len(digest) + 1))[:self.DIM]
            rows.append(np.frombuffer(raw, dtype=np.uint8).astype(np.float64))
        return np.asarray(rows)


@pytest.fixture(autouse=True)
def _offline_llm_seam(monkeypatch):
    """Install the offline MockModel seam for this module (#822).

    ``test_hosted_capture_embeds_turn_points`` drives ``POST /v1/sessions``,
    which 503s before the turn loop unless a provider key OR this seam is
    present (``_llm_provider_available``). The source module's autouse fixture
    does NOT travel with ``from tests.test_hosted_api import client``, so a
    keyless CI run would 503 and a keyed machine would make a REAL network LLM
    call. The SDK tests here call ``_keyless``, which deletes the seam and every
    provider key.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture
def embedder(monkeypatch):
    """Pin a HEALTHY deterministic embedder for the write and the read path."""
    emb = _FakeEmbedder()
    monkeypatch.setattr(
        EmbeddingModel, "get",
        classmethod(lambda cls, load_timeout=None: emb))
    EmbeddingModel._reset()
    yield emb
    EmbeddingModel._reset()


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(str(tmp_path / "t.db"))
    try:
        yield s
    finally:
        s.close()


def _keyless(monkeypatch) -> None:
    """Make the capture keyless — no LLM extraction runs (no network, no mock).

    The turns are still written by the mechanical loop under test; clearing the
    mock seam AND every provider key makes any extraction attempt fail loudly
    instead of quietly serving a mock.
    """
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    from tortoise.sdk import _build_session_llm_extractor
    assert _build_session_llm_extractor() is None, "keys leaked into the test"


CONV = [
    {"role": "user",
     "content": "The auth dead-end is the top issue; ship serve --http first."},
    {"role": "assistant",
     "content": "Agreed, the website config looks like the root cause."},
]


def _turn_rows(sdk: TortoiseSDK, session_id: str):
    return sdk._get_proj().g.query(
        "MATCH (t:Point) WHERE t.id STARTS WITH $p "
        "RETURN t.id, t.content, t.embedding ORDER BY t.id",
        params={"p": f"{session_id}_t"}).result_set


# ── 1. The write path stores the encoder's own vector ─────────────────────

def test_captured_turn_embedding_matches_the_query_encoder(sdk, embedder,
                                                           monkeypatch):
    """The stored turn vector is the SAME vector the query leg encodes.

    Pins model + dimension + normalisation together: the write goes through
    ``embeddings.encode_batch_for_store`` → ``compute_embeddings`` (the seam
    ``create_point``'s ``compute_embedding`` delegates to, so turn and extracted
    Points can never diverge) and is compared against the EXACT call the read
    path makes (``EmbeddingModel.get().encode([q])[0]``). A dimension mutation
    (wrong-length vector) fails the length assertion; a normalisation mutation
    (a unit-norm rewrite on one side only) fails the value comparison.
    """
    _keyless(monkeypatch)
    res = sdk.capture_session(CONV, session_id="sess-4194")
    assert res["turns"] == len(CONV), res

    rows = _turn_rows(sdk, "sess-4194")
    assert [r[0] for r in rows] == [f"sess-4194_t{i}" for i in range(len(CONV))]

    # The dimension the READ path's encoder produces — never a hardcoded 384,
    # so the test tracks the embedder rather than a magic number.
    query_dim = len(embedder.encode(["anything"])[0])
    for tid, content, stored in rows:
        assert stored is not None, (
            f"{tid}: turn stored WITHOUT an embedding — the dense leg is inert")
        assert len(stored) == query_dim, (
            f"{tid}: stored dim {len(stored)} != query-encoder dim {query_dim}")
        # Write-side: the seam ``create_point``'s ``compute_embedding``
        # delegates to (single-vs-batched equality for the same text).
        assert np.allclose(stored, compute_embedding(content), atol=1e-6), tid
        # Query-side: the exact call the read path makes. (For content at or
        # under the 512-word encode cap this is the same string the write
        # encoded; a LONGER turn is word-truncated before write-encoding —
        # covered by test_long_turn_and_coerced_content below.)
        qvec = embedder.encode([content])[0]
        assert np.allclose(stored, qvec, atol=1e-6), (tid, "stored != query")
        # Normalisation equality: the stored vector is the encoder's raw,
        # un-normalised output (a unit-norm rewrite on either side would make
        # the vector above disagree, since these fake vectors are not unit).
        assert np.isclose(np.linalg.norm(stored), np.linalg.norm(qvec),
                          rtol=1e-5), tid
        assert not np.isclose(np.linalg.norm(stored), 1.0, rtol=1e-3), (
            f"{tid}: stored vector was L2-normalised — the query encoder does "
            "not normalise, so the two spaces would disagree")


# ── 2. The dense leg now actually returns the captured turn ───────────────

def test_dense_leg_returns_the_captured_turn(sdk, embedder, monkeypatch):
    """Searching the turn's own content finds it THROUGH the dense leg.

    ``run_vector_query`` is the vector leg in isolation — no lexical leg is
    involved — so its returning the turn proves the leg now has material. The
    leg trace records it as ran/not-degraded with a non-zero count, and the
    product declaration surface reports the read as hybrid.
    """
    _keyless(monkeypatch)
    sdk.capture_session(CONV, session_id="sess-4194-dense")
    proj = sdk._get_proj()
    turn_id = "sess-4194-dense_t0"
    turn_text = proj.g.query(
        "MATCH (t:Point {id:$id}) RETURN t.content",
        params={"id": turn_id}).result_set[0][0]

    trace: list[dict] = []
    qvec = embedder.encode([turn_text])[0].tolist()
    hits = run_vector_query(
        proj.g, qvec, limit=10,
        is_embedded=getattr(proj, "_is_embedded", True),
        entity_type="point", leg_trace=trace)
    assert turn_id in [h[0] for h in hits], (
        f"the turn's own vector did not surface it: {[h[0] for h in hits]}")

    vec = next(e for e in trace if e["leg"] == "vector")
    assert vec["ran"] is True, vec
    assert vec["degraded"] is False, vec
    assert vec["count"] >= 1, vec

    # The product's own declaration agrees: the read is hybrid (not a declared
    # single-leg/keyword-only read).
    legs = sdk.retrieval_legs(turn_text, limit=5)
    assert legs["declared_degraded_read"] is None, legs
    assert legs["hybrid"] is True, legs
    assert any(e["leg"] == "vector" and e["count"] >= 1
               for e in legs["legs"]), legs["legs"]


# ── 3. Negative control: no embedder ⇒ turn stored, leg DECLARED impaired ──

def test_no_embedder_still_stores_the_turn_and_declares_the_leg_impaired(
        sdk, monkeypatch):
    """Degradation stays VISIBLE: no embedder must never read as ``available``.

    The turn is still stored (the capture is unconditional, #3892) with NO
    vector — never a junk one. The read path then declares its vector leg
    not-run under the shared vocabulary (``no_embedder`` /
    ``vector_leg_unavailable``) and reports ``hybrid: False``.
    """
    _keyless(monkeypatch)
    monkeypatch.setattr(
        EmbeddingModel, "get",
        classmethod(lambda cls, load_timeout=None: None))
    EmbeddingModel._reset()

    res = sdk.capture_session(CONV, session_id="sess-4194-noemb")
    assert res["turns"] == len(CONV), res

    rows = _turn_rows(sdk, "sess-4194-noemb")
    assert [r[0] for r in rows] == [
        f"sess-4194-noemb_t{i}" for i in range(len(CONV))]
    for tid, content, stored in rows:
        assert content, (tid, "the turn must still be stored")
        assert stored is None, (
            f"{tid}: no embedder yet a vector was written — a fabricated "
            "vector is worse than an honest NULL")

    legs = sdk.retrieval_legs("auth dead-end", limit=5)
    marker = legs["declared_degraded_read"]
    assert marker is not None, (
        "a vector leg that never ran was reported as available")
    assert marker["vector_leg_unavailable"] is True, marker
    assert marker["missing_legs"] == ["vector"], marker
    assert marker["reason"] == "no_embedder", marker
    assert marker["degraded_read"] is True, marker
    assert marker["hybrid"] is False, marker
    assert legs["hybrid"] is False, legs


def test_recapture_without_an_embedder_preserves_the_stored_vector(
        sdk, embedder, monkeypatch):
    """A re-capture must never NULL an already-stored turn vector.

    The write is a MERGE (re-captures happen on the same deterministic
    ``{session}_t{i}`` id), so an unguarded ``vecf32($emb)`` with ``$emb=NULL``
    would REMOVE the property on a capture that happens to run while the
    embedder is down — losing material a healthy earlier capture stored. The
    capture write's own three-way CASE guard preserves the stored vector when
    the content is unchanged.
    """
    _keyless(monkeypatch)
    sdk.capture_session(CONV, session_id="sess-4194-recap")
    before = _turn_rows(sdk, "sess-4194-recap")
    assert before and all(r[2] is not None for r in before), before

    # The embedder is now unavailable and the SAME session is re-captured.
    monkeypatch.setattr(
        EmbeddingModel, "get",
        classmethod(lambda cls, load_timeout=None: None))
    EmbeddingModel._reset()
    res = sdk.capture_session(CONV, session_id="sess-4194-recap")
    assert res["turns"] == len(CONV), res

    after = _turn_rows(sdk, "sess-4194-recap")
    assert len(after) == len(before)
    for (_bid, _bc, bvec), (aid, _ac, avec) in zip(before, after, strict=True):
        assert aid == _bid
        assert avec is not None, (
            f"{aid}: re-capture without an embedder REMOVED the stored vector")
        assert np.allclose(bvec, avec, atol=1e-6), aid


def test_long_turn_and_coerced_content_still_store_the_encoded_text(
        sdk, embedder, monkeypatch):
    """The vector is computed over exactly the STORED text, at both edges.

    Two shapes the #721 coercion / the 512-word cap exist for: a turn longer
    than the encode cap (the write word-truncates via `_truncate_for_embedding`,
    while the node stores the full capped text) and non-string role/content
    (None -> "unknown"/"", truthy non-strings -> `str()`). Pinning both keeps a
    future edit to `_capture_turn_texts` from silently breaking the
    stored-text == encoded-text parity.
    """
    _keyless(monkeypatch)
    long_turn = " ".join(f"w{i}" for i in range(1200))  # > 512 words
    sdk.capture_session(
        [{"role": "user", "content": long_turn},
         {"role": None, "content": 123}],
        session_id="sess-4194-long")
    rows = {r[0]: (r[1], r[2]) for r in _turn_rows(sdk, "sess-4194-long")}

    # The long turn: the node stores the full text; the vector is the encode of
    # its 512-word truncation (not the raw stored string).
    stored0, emb0 = rows["sess-4194-long_t0"]
    assert stored0 == "[user] " + long_turn[:5000], stored0[:60]
    assert emb0 is not None
    truncated = " ".join(stored0.split()[:512])
    assert np.allclose(emb0, embedder.encode([truncated])[0], atol=1e-6)

    # Non-string role/content: coerced exactly as stored, and the SAME string
    # was what the embedder encoded.
    stored1, emb1 = rows["sess-4194-long_t1"]
    assert stored1 == "[unknown] 123"
    assert emb1 is not None
    assert np.allclose(emb1, embedder.encode([stored1])[0], atol=1e-6)


def test_recapture_with_changed_content_and_no_embedder_clears_the_stale_vector(
        sdk, embedder, monkeypatch):
    """A stale vector must NEVER survive a changed re-capture.

    The P1 this pins: capture with a healthy embedder, then re-capture the same
    deterministic id with DIFFERENT content and no embedder. A guard that
    simply preserved ``t.embedding`` on ``$emb = NULL`` would leave the node
    carrying the OLD text's vector — the dense leg would then rank the turn by
    text no longer on it, the "mismatched vector runs and returns garbage"
    trap. The write clears it instead.
    """
    _keyless(monkeypatch)
    sdk.capture_session(
        [{"role": "user", "content": "ORIGINAL alpha text"}],
        session_id="sess-4194-stale")
    before = _turn_rows(sdk, "sess-4194-stale")
    assert before and before[0][2] is not None, before

    monkeypatch.setattr(
        EmbeddingModel, "get",
        classmethod(lambda cls, load_timeout=None: None))
    EmbeddingModel._reset()
    sdk.capture_session(
        [{"role": "user", "content": "COMPLETELY DIFFERENT beta text"}],
        session_id="sess-4194-stale")

    after = _turn_rows(sdk, "sess-4194-stale")
    assert after[0][1] == "[user] COMPLETELY DIFFERENT beta text"
    assert after[0][2] is None, (
        "a stale vector survived a changed re-capture — the dense leg would "
        "rank this turn by text no longer on the node")


def test_rebuild_recomputes_the_turn_embedding(tmp_path, embedder, monkeypatch):
    """live == rebuild for the new field.

    The journal deliberately omits ``embedding`` and
    ``projection/entities._upsert_point_props`` recomputes it from ``content``
    on replay — so a rebuilt turn Point must still carry the (same) vector.
    Without this, a rebuild could silently drop the field the whole fix is
    about, exactly the #3947 class.
    """
    _keyless(monkeypatch)
    from tortoise.log import EventLog
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    s = TortoiseSDK(str(tmp_path / "t.db"), event_log_path=log_path)
    try:
        s.capture_session(CONV, session_id="sess-4194-rebuild")
        before = _turn_rows(s, "sess-4194-rebuild")
        assert before and all(r[2] is not None for r in before), before

        s._get_proj().rebuild(EventLog(log_path))

        after = _turn_rows(s, "sess-4194-rebuild")
        assert [r[0] for r in after] == [r[0] for r in before]
        for (_bid, _bc, bvec), (aid, _ac, avec) in zip(before, after,
                                                      strict=True):
            assert aid == _bid
            assert avec is not None, (
                f"{aid}: the rebuilt turn lost its embedding")
            assert np.allclose(bvec, avec, atol=1e-6), aid
    finally:
        s.close()


def test_wrong_width_model_output_degrades_to_no_vector(sdk, monkeypatch, embedder):
    """A width the STORE cannot hold degrades to no vector (#4194, #4280).

    The width constraint belongs to the Point HNSW index — which exists only on
    the non-embedded lane — so the STORE declares it
    (``FalkorProjection.required_embedding_dim``) and the write path applies it
    at the store-scoped entry point (``embeddings.encode_for_store`` /
    ``encode_batch_for_store``). Asserted there, and then end-to-end: a
    wrong-width model captured into a store that declares :data:`EMBEDDING_DIM`
    stores the turn with NO vector instead of handing ``vecf32`` a wrong-space
    vector, and the same model into a store that declares ``None`` (no index —
    the embedded brute-force lane) stores its own vector.

    ⛔ The encoder SEAM (``compute_embedding`` / ``compute_embeddings``)
    deliberately does NOT apply the guard and keeps its own narrow call shape:
    it is a widely-REPLACED interception point (``tools/longmem_eval/
    encode_cache.py`` and the longmem eval doubles swap the function itself),
    so a caller-side width keyword would raise ``TypeError`` inside every
    replacement and be swallowed by the write paths' ``except Exception`` —
    the same silent degrade in a new place (#4280 review).
    """
    from tortoise.embeddings import (
        EMBEDDING_DIM,
        compute_embedding,
        compute_embeddings,
        encode_batch_for_store,
        encode_for_store,
    )
    from tortoise.projection import FalkorProjection

    assert len(embedder.encode(["x"])[0]) == EMBEDDING_DIM

    class _WrongDim:
        def encode(self, texts, batch_size=32, show_progress_bar=False):
            return np.zeros((len(texts), EMBEDDING_DIM - 1))

    monkeypatch.setattr(
        EmbeddingModel, "get",
        classmethod(lambda cls, load_timeout=None: _WrongDim()))
    EmbeddingModel._reset()

    # The seam encodes whatever the model returns (an index-less lane holds it)…
    assert len(compute_embedding("a")) == EMBEDDING_DIM - 1
    assert len(compute_embeddings(["a", "b"])[0]) == EMBEDDING_DIM - 1
    # … and the STORE's declared width is what degrades it.
    assert encode_for_store("a", EMBEDDING_DIM) is None
    assert encode_batch_for_store(["a", "b"], EMBEDDING_DIM) == [None, None]
    # No index declared → the encoder's own width governs (nothing dropped).
    assert encode_for_store("a", None) is not None
    assert encode_batch_for_store(["a"], None)[0] is not None

    # End-to-end: an INDEXED store (declares EMBEDDING_DIM) stores the turn
    # with no vector — the turn itself still lands.
    _keyless(monkeypatch)
    monkeypatch.setattr(FalkorProjection, "required_embedding_dim",
                        property(lambda self: EMBEDDING_DIM))
    res = sdk.capture_session(CONV, session_id="sess-4194-wrongdim")
    assert res["turns"] == len(CONV), res
    rows = _turn_rows(sdk, "sess-4194-wrongdim")
    assert len(rows) == len(CONV)
    for tid, content, stored in rows:
        assert content, f"{tid}: the turn must still be stored"
        assert stored is None, (
            f"{tid}: a {EMBEDDING_DIM - 1}-wide vector was stored in a "
            f"{EMBEDDING_DIM}-wide index")

    # Same model, a store with NO index: the encoder's own width is stored.
    monkeypatch.setattr(FalkorProjection, "required_embedding_dim",
                        property(lambda self: None))
    res2 = sdk.capture_session(CONV, session_id="sess-4194-noidx")
    assert res2["turns"] == len(CONV), res2
    for tid, _content, stored in _turn_rows(sdk, "sess-4194-noidx"):
        assert stored is not None, (
            f"{tid}: the index-less lane must keep the encoder's vector")
        assert len(stored) == EMBEDDING_DIM - 1, tid


def test_required_embedding_dim_follows_the_index_not_the_mode(sdk, monkeypatch):
    """The declared width follows the INDEX, not the deployment flag (#4280).

    Three lanes have NO Point HNSW index and all three read through
    ``run_vector_query``'s dimension-agnostic brute-force branch: an embedded
    store (index creation is skipped by design), and a NON-embedded store whose
    index creation did not succeed (engine older than 4.x, or both creation
    attempts raised). The store must therefore declare no width on all three —
    keying this on ``_is_embedded`` instead re-armed the #4280 shape on the
    last two (review finding, PR #4280). Only a store that HAS the index
    declares :data:`EMBEDDING_DIM`.
    """
    from tortoise.embeddings import EMBEDDING_DIM

    proj = sdk._get_proj()

    # (1) No index at all — the embedded lane. The state is FORCED rather than
    #     read from the ambient store: under ``TORTOISE_DB_URI`` the SDK's
    #     ``db_path`` construction is redirected to a server, so an assertion on
    #     ``_is_embedded`` would itself be lane-dependent (#4280 re-review).
    monkeypatch.setattr(proj, "_is_embedded", True)
    monkeypatch.setattr(proj, "_vector_index_api", None)
    assert proj.required_embedding_dim is None, (
        "an index-less store must not declare a width — its read path "
        "brute-force scans and any self-consistent width is storable")

    # (2) NON-embedded, but no index exists either: an engine older than 4.x,
    #     or both creation attempts failed. The read path is STILL the
    #     dimension-agnostic brute-force branch.
    monkeypatch.setattr(proj, "_is_embedded", False)
    assert proj.required_embedding_dim is None, (
        "the width follows the INDEX, not the deployment mode (#4280 review)")

    # (3) The index exists → the width it was created with.
    monkeypatch.setattr(proj, "_vector_index_api", "cypher")
    assert proj.required_embedding_dim == EMBEDDING_DIM


# ── 4. The hosted write path is the same write ────────────────────────────

def test_hosted_capture_embeds_turn_points(client, monkeypatch, embedder):
    """``hosted_api._capture_session_impl`` duplicates the SDK turn loop — the
    two are kept byte-identical by design (#1532), so the hosted path must
    store the same embedding. Without this the hosted writer could silently
    regress to the inert dense leg while the SDK test stayed green."""
    conv = [{"role": "user",
             "content": "hosted turn embedding parity check for #4194"}]
    r = client.post("/v1/sessions", json={"conversation": conv})
    assert r.status_code == 200, r.text

    import tortoise.hosted_api as ha_mod
    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    rows = sdk._get_proj().g.query(
        "MATCH (t:Point) WHERE t.is_episodic=true "
        "RETURN t.id, t.content, t.embedding ORDER BY t.id").result_set
    assert rows, "hosted capture wrote no turn Point"
    for tid, content, stored in rows:
        assert stored is not None, (
            f"{tid}: hosted turn write skipped the embedding")
        assert len(stored) == len(embedder.encode(["x"])[0]), tid
        assert np.allclose(stored, embedder.encode([content])[0], atol=1e-6), tid
