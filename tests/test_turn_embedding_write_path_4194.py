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
from tortoise.embeddings import EmbeddingModel
from tortoise.sdk import TortoiseSDK


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



# ── 2. The dense leg now actually returns the captured turn ───────────────



# ── 3. Negative control: no embedder ⇒ turn stored, leg DECLARED impaired ──













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
