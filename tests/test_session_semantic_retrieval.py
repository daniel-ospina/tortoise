"""#3519 — a captured session is retrievable by MEANING, not only by words.

**The pinned approach (the issue's own "pin ONE and say why").** Embed the
**turn Points** — per turn, at the turn ``MERGE`` on the capture write path —
rather than chunking + embedding the transcript as a unit. Rationale:

* the query must retrieve **the turn it came from**, not a transcript blob
  whose provenance is lost, so the turn Point is the right retrieval grain;
* it needs **no new node kind / chunk schema** (no ontology change) and reuses
  the existing ``Point.embedding`` property + vector index, so the
  deterministic ``{session_id}_t{i}`` ids and idempotent re-capture survive;
* the transcript-unit alternative would add a representation and a backfill
  surface this slice does not own.

**What that approach cost, and where it already landed.** The write half was
built and measured under **#4194** (``sdk._capture_turn_embeddings`` →
``embeddings.encode_batch_for_store`` → the same ``compute_embeddings`` seam
``create_point`` uses; ONE batched local-model call per capture, not one call
per turn). Recurring cost: one local batch encode per capture over the turn
texts, proportional to ``len(conversation)``. This file is the **named
acceptance test** the issue requires (``tests/test_session_semantic_retrieval.py``):
it asserts a **semantic** query — a paraphrase that shares no content word with
the turn — retrieves the captured turn, and that the turn came to it free of
any lexical (sparse-leg) score.

Two layers, so the acceptance does not depend on a model download:

* the **hermetic** cases stub ``EmbeddingModel.get`` with a meaning-table fake
  (the same concept-axis precedent as ``test_session_semantic_search.py``) and
  always run — they pin the *plumbing*: the stored turn vector, the read path's
  use of it, and the regression control;
* the **real-embedder** case runs the pinned ``BAAI/bge-small-en-v1.5`` model
  (skipped when unavailable) and asserts the same paraphrase hit in the real
  semantic space.
"""
from __future__ import annotations

import re

import numpy as np
import pytest

from tortoise.embeddings import EMBEDDING_MODEL, EmbeddingModel
from tortoise.sdk import TortoiseSDK

# The turn the query must find — and the paraphrase that must find it. They
# share NO content word (``the``/``of`` are stopwords); the ONLY relation
# between them is meaning.
TARGET_TURN = (
    "The login flow dead-ends whenever the session token expires halfway "
    "through a request."
)
DISTRACTOR_TURN = (
    "We baked sourdough bread and the crust came out perfect."
)
PARAPHRASE = "why do people get locked out of their account in the middle of working"

_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for",
    "from", "in", "into", "is", "it", "its", "of", "on", "or", "out",
    "that", "the", "their", "they", "this", "through", "to", "we", "what",
    "when", "why", "with",
})


def _content_tokens(text: str) -> set[str]:
    """Word tokens minus stopwords — the literal-overlap floor a semantic hit
    must NOT need (``why are people…`` must not match by keyword)."""
    words = re.findall(r"[a-z0-9'-]+", text.lower())
    return {w for w in words if w not in _STOPWORDS}


# ── The meaning-table fake (hermetic layer) ────────────────────────────────
#
# Each tuple is one CONCEPT AXIS: every surface form in it projects onto the
# same dimension. The table is what supplies the semantic relation the real
# model supplies in production; it lives here so the plumbing assertions are
# deterministic, offline and model-free. The paraphrase and the target turn
# share NO surface form and NO content token — they share an AXIS.
_CONCEPT_AXES = (
    frozenset({
        "login", "log in", "logged", "logged out", "logout", "sign-in",
        "signin", "session", "token", "credential", "expires", "expire",
        "locked", "locked out", "account",
    }),
    frozenset({"database", "postgres", "sql", "query", "index", "migration"}),
    frozenset({"sourdough", "baked", "bread", "crust", "recipe", "oven"}),
)


#: The store may declare a required vector width (a Point HNSW index does,
#: ``FalkorProjection.required_embedding_dim``), and a narrower vector is
#: DROPPED rather than stored (#4280). The fake pads to the production width
#: so the hermetic case exercises the same indexed lane the real model does;
#: only the first ``len(_CONCEPT_AXES)`` dims carry meaning.
_FAKE_DIM = 384


def _axis_vec(text: str) -> list[float]:
    lowered = str(text).lower()
    vec = [0.0] * _FAKE_DIM
    for i, axis in enumerate(_CONCEPT_AXES):
        if any(form in lowered for form in axis):
            vec[i] = 1.0
    return vec


class _MeaningTableEmbedder:
    """Deterministic encoder whose similarity is *meaning* (axis overlap).

    Distinct meanings get distinct (orthogonal) vectors; the same meaning gets
    the same vector regardless of the words used. That is the property the
    real embedder supplies — the fake makes it a fixture, not a model call.
    """

    def encode(self, texts, batch_size=32, show_progress_bar=False):
        return np.asarray([_axis_vec(t) for t in texts], dtype=np.float64)


@pytest.fixture
def meaning_embedder(monkeypatch):
    emb = _MeaningTableEmbedder()
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
    """Capture with NO LLM extraction — the turns are still written (and
    embedded); no provider key can reach the network from a test."""
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    from tortoise.sdk import _build_session_llm_extractor
    assert _build_session_llm_extractor() is None, "keys leaked into the test"


def _write(sdk: TortoiseSDK, session_id: str, turns: list[str]):
    res = sdk.capture_session(
        [{"role": "user", "content": t} for t in turns], session_id=session_id)
    assert res["turns"] == len(turns), res
    return res


def _retrieve(sdk: TortoiseSDK, query: str):
    """One read through the product surface; returns (hits, leg_trace)."""
    trace: list[dict] = []
    hits = sdk.recall_state(query, limit=10, leg_trace=trace)
    return {h["id"]: h for h in hits}, trace


def _scores(hit: dict) -> dict:
    return hit.get("scores") or {}


def _vector_legs(trace: list[dict]) -> list[dict]:
    return [e for e in trace if e["leg"] == "vector" and e["ran"]]


def _stored_vector(sdk: TortoiseSDK, point_id: str):
    rows = sdk._get_proj().g.query(
        "MATCH (t:Point {id:$id}) RETURN t.embedding",
        params={"id": point_id}).result_set
    assert rows, f"{point_id} was not stored"
    return rows[0][0]


# ── 1. The acceptance: a paraphrase retrieves the captured turn ────────────

def test_semantic_paraphrase_retrieves_the_captured_turn(
        sdk, meaning_embedder, monkeypatch):
    """A query sharing NO content word with the turn still retrieves it.

    The target's presence must come from the VECTOR leg: the target carries no
    sparse score at all, while the sparse leg demonstrably HAD material (it
    matched the unrelated turn on a shared common word). A retrieval that did
    not use meaning cannot produce this result. The stored vector is the same
    one the query encoder produces — written over exactly the stored turn text
    (``sdk._capture_turn_texts``).
    """
    _keyless(monkeypatch)
    assert not (_content_tokens(PARAPHRASE) & _content_tokens(TARGET_TURN)), (
        "the fixture must not share content words — otherwise the hit could "
        "be a keyword hit and this test would prove nothing")

    _write(sdk, "sess-3519", [TARGET_TURN, DISTRACTOR_TURN])

    # The turn carries the vector its dense leg searches (the #4194 write).
    stored = _stored_vector(sdk, "sess-3519_t0")
    assert stored is not None, (
        "the captured turn has no embedding — the dense leg is inert for it")
    assert np.allclose(stored, meaning_embedder.encode(
        [f"[user] {TARGET_TURN}"])[0], atol=1e-6)

    hits, trace = _retrieve(sdk, PARAPHRASE)

    assert "sess-3519_t0" in hits, (
        f"the paraphrase did not retrieve the turn it means: {sorted(hits)}")
    target = _scores(hits["sess-3519_t0"])
    # SEMANTIC, not lexical: the target carries NO sparse score. This is the
    # per-hit proof — the sparse leg DID have material (it matched the
    # unrelated turn on a shared common word), yet the target was not reached
    # by it. A zero leg-count would additionally be lane-dependent (the
    # embedded lane has no FTS index), so the assertion is on the hit.
    assert target.get("fts") is None, (
        f"the target was matched lexically — not a semantic hit: {target}")
    assert target.get("vector") is not None, target

    # SELECTIVE, not merely PRESENT. With a 2-turn corpus and ``limit=10`` the
    # dense leg returns every embedded point regardless of meaning, so ``in
    # hits`` alone is satisfied by ANY query — measured: an unrelated
    # ``"xyzzy plugh frobnicate"`` query also returns this turn (vector 0.5).
    # Presence therefore pins that the turn was embedded and searched (the
    # regression control below covers its absence), but it cannot detect a
    # dense leg that matches by anything other than meaning. The acceptance
    # must assert the paraphrase ranks the turn it MEANS above the unrelated
    # turn.
    assert "sess-3519_t1" in hits, (
        f"the unrelated turn was not returned, so there is nothing to rank "
        f"against — this assertion would be vacuous: {sorted(hits)}")
    distractor = _scores(hits["sess-3519_t1"])
    assert target["vector"] > distractor["vector"], (
        "the paraphrase did not rank the turn it means above the unrelated "
        f"turn — the dense leg is not selective by meaning: "
        f"target={target} distractor={distractor}")

    # ...and the leg that carried it ran healthy with material to return.
    healthy = [e for e in _vector_legs(trace)
               if e["degraded"] is False and e["count"] >= 1]
    assert healthy, trace


# ── 2. Regression control: without the turn vector the same query MISSES ───

def test_paraphrase_misses_when_the_turn_was_not_embedded(
        sdk, meaning_embedder, monkeypatch):
    """The pre-#4194 write shape (turn stored, NO vector) fails the same
    assertion — so the acceptance above is load-bearing, not incidental.

    This is the "fails before" half of the red→green proof: restoring the old
    write behaviour (no embedding on the turn MERGE) must reintroduce the
    lexical-only miss the issue reports.
    """
    _keyless(monkeypatch)
    import tortoise.sdk as sdk_mod

    monkeypatch.setattr(
        sdk_mod, "_capture_turn_embeddings",
        lambda texts, expected_dim=None: [None] * len(texts))

    _write(sdk, "sess-3519-unembedded", [TARGET_TURN, DISTRACTOR_TURN])
    assert _stored_vector(sdk, "sess-3519-unembedded_t0") is None, (
        "the control must reproduce the un-embedded turn shape")

    hits, trace = _retrieve(sdk, PARAPHRASE)
    assert "sess-3519-unembedded_t0" not in hits, (
        "an un-embedded turn was retrieved semantically — the control no "
        f"longer reproduces the defect: {sorted(hits)}")
    # The dense leg had no material at all — the honest degraded declaration.
    assert any(e.get("reason") == "no_embeddings"
               for e in _vector_legs(trace)), trace


# ── 3. The real semantic space (skipped when the model is unavailable) ────

def test_real_embedder_paraphrase_retrieves_the_captured_turn(sdk, monkeypatch):
    """The same acceptance in the production semantic space (bge-small).

    The meaning-table test above proves the plumbing; this one proves the
    shipped model actually places the paraphrase next to the turn. Guarded:
    the model is a heavy local download, so the case skips (visibly) when
    ``sentence_transformers`` or the pinned checkpoint is unavailable — the
    hermetic cases above remain the always-run guard.
    """
    pytest.importorskip("sentence_transformers")
    _keyless(monkeypatch)
    EmbeddingModel._reset()
    try:
        model = EmbeddingModel.get()
    except Exception:
        model = None
    if model is None:
        pytest.skip(f"{EMBEDDING_MODEL} unavailable — real-embedder case skipped")

    _write(sdk, "sess-3519-real", [TARGET_TURN, DISTRACTOR_TURN])
    assert _stored_vector(sdk, "sess-3519-real_t0") is not None

    hits, trace = _retrieve(sdk, PARAPHRASE)
    assert "sess-3519-real_t0" in hits, (
        f"the real embedder did not put the paraphrase near the turn: "
        f"{sorted(hits)}")
    assert _scores(hits["sess-3519-real_t0"]).get("fts") is None, (
        f"the target was matched lexically — not a semantic hit: "
        f"{_scores(hits['sess-3519-real_t0'])}")
    # Same selectivity requirement as the hermetic case: the shipped model must
    # rank the turn the paraphrase MEANS above the unrelated turn, not merely
    # return it from a corpus small enough to return everything.
    assert "sess-3519-real_t1" in hits, (
        f"the unrelated turn was not returned, so there is nothing to rank "
        f"against: {sorted(hits)}")
    real_target = _scores(hits["sess-3519-real_t0"])
    real_distractor = _scores(hits["sess-3519-real_t1"])
    assert real_target["vector"] > real_distractor["vector"], (
        "the real embedder did not rank the paraphrase above the unrelated "
        f"turn: target={real_target} distractor={real_distractor}")
    assert any(e["count"] >= 1 for e in _vector_legs(trace)), trace


# ── 4. Guard: the embedding is over the STORED text, not a second rendering ─

def test_turn_vector_is_over_the_stored_turn_text(sdk, meaning_embedder,
                                                  monkeypatch):
    """The vector encodes ``[role] <content>`` — the SAME string the node
    stores (``sdk._capture_turn_texts``). A vector computed over some other
    rendering would match text the node does not hold, the dense-leg lie the
    #4194 write path exists to prevent."""
    _keyless(monkeypatch)
    _write(sdk, "sess-3519-text", [TARGET_TURN])
    rows = sdk._get_proj().g.query(
        "MATCH (t:Point {id:'sess-3519-text_t0'}) RETURN t.content, t.embedding"
    ).result_set
    stored_text, stored_vec = rows[0]
    assert np.allclose(stored_vec, meaning_embedder.encode([stored_text])[0],
                       atol=1e-6), stored_text
