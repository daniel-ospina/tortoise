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
    ``compute_embedding`` (what ``create_point`` uses, so turn and extracted
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
        # Write-side: the shared function create_point uses.
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
    CASE guard (the same one ``_upsert_point_props`` uses) keeps it.
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
    for (bid, _bc, bvec), (aid, _ac, avec) in zip(before, after, strict=True):
        assert aid == bid
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
