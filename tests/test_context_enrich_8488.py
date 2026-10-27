"""#8488 scope 1 — context-enriched turn embeddings, gated OFF by default.

What this file pins, and why each test exists:

* the GATE: ``TORTOISE_CONTEXT_ENRICH`` is fail-safe OFF, and OFF is the
  byte-identical pre-#8488 write (no ``embeddingText`` on the node, the vector
  is computed from the raw stored text);
* the SCHEMA (owner ruling #8932, shape A′): ON stores ``embeddingText`` (the
  enriched text) beside the raw ``content``, and the ONE vector is computed
  from ``embeddingText`` — the vector can never describe text the node does not
  hold (#4194);
* the on/off retrieval-equivalence property: the dense leg returns the RAW
  turn for the RAW query when OFF, and the ENRICHED turn for the enriched
  meaning when ON — a test that could not tell on from off would not red on
  the gate mutation;
* the enrichment SEAM: identity without a rewrite function, a per-turn rewrite
  with one (threading the neighbour window), and fail-soft to raw on any
  failure — a capture must never store a half-enriched batch;
* the ON→OFF re-capture: a stale enriched text must not survive a re-capture
  whose flag is off (the #4194 lie a vector no longer describes);
* raw retrieval unchanged: ``content`` stays in the fulltext field set, so a
  query that matched before still matches with enrichment on.

Fixture note (honest scope, same as tests/test_bm25_lexical_8488.py): the
dense leg is driven by a small lexicon stand-in embedder installed at the
``EmbeddingModel.get`` seam, because the real bge-small model is not installed
in this environment (no ``sentence-transformers``). The stand-in gives every
in-lexicon concept word a direction, so it proves the MECHANISM (the vector
changes when the enriched text changes) — not a live numeric improvement, which
is #8486's measurement and has not been run. The LLM rewrite itself is tested
through the ``complete_fn`` seam with a deterministic fake model, never a
network call.
"""
from __future__ import annotations

from typing import ClassVar

import numpy as np
import pytest

from tortoise.context_enrich import (
    ENRICH_ENV,
    enrich_enabled,
    enrich_turn_texts,
)
from tortoise.embeddings import EmbeddingModel, compute_embedding
from tortoise.ids import content_hash
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import run_vector_query

pytestmark = pytest.mark.embedded_only


class _LexiconEmbedder:
    """Concept-word stand-in embedder (see the module docstring)."""

    DIM = 4
    LEXICON: ClassVar[dict[str, tuple[float, ...]]] = {
        "release": (1.0, 0.0, 0.0, 0.0),
        "billing": (0.0, 1.0, 0.0, 0.0),
        "auth": (0.0, 0.0, 1.0, 0.0),
        "deploy": (0.0, 0.0, 0.0, 1.0),
    }

    def encode(self, texts, batch_size=32, show_progress_bar=False):
        rows = []
        for text in texts:
            vec = np.zeros(self.DIM, dtype=np.float64)
            for tok in str(text).lower().split():
                direction = self.LEXICON.get(tok)
                if direction is not None:
                    vec += np.asarray(direction, dtype=np.float64)
            norm = float(np.linalg.norm(vec))
            rows.append(vec / norm if norm else vec)
        return np.stack(rows).astype(np.float32)


class _Rewriter:
    """Deterministic rewrite model — resolves "the release" to "the auth flow"
    (as if from the neighbour window), so the enriched text differs from the
    raw and maps to a different lexicon direction."""

    def complete(self, *, system, user):
        target = user.split("TARGET turn (rewrite this one):\n", 1)[1]
        target = target.split("\n\n", 1)[0]
        return target.replace("the release is blocked", "the auth flow is blocked")


@pytest.fixture
def embedder(monkeypatch):
    stub = _LexiconEmbedder()
    monkeypatch.setattr(EmbeddingModel, "get", classmethod(
        lambda cls, load_timeout=None: stub))
    EmbeddingModel._reset()
    yield stub
    EmbeddingModel._reset()


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(str(tmp_path / "t.db"))
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def enrichment_on(monkeypatch):
    """Enable the gate AND pin a deterministic rewrite model (no network)."""
    from tortoise import model_adapters
    monkeypatch.setenv(ENRICH_ENV, "1")
    monkeypatch.setattr(model_adapters, "build_extractor_model",
                        lambda *a, **kw: _Rewriter())
    _keyless(monkeypatch)


def _keyless(monkeypatch) -> None:
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def _turn_rows(sdk, session_id):
    return sdk._get_proj().g.query(
        "MATCH (t:Point) WHERE t.id STARTS WITH $p "
        "RETURN t.id, t.content, t.embeddingText, t.embedding ORDER BY t.id",
        params={"p": f"{session_id}_t"}).result_set


def _query_vec(embedder, text):
    return embedder.encode([text])[0].tolist()


CONV = [
    {"role": "user", "content": "the release is blocked"},
    {"role": "assistant", "content": "the auth flow is the root cause"},
]


# ── 1. The gate ────────────────────────────────────────────────────────────

def test_enrich_is_off_by_default(monkeypatch):
    """The gate is fail-safe OFF: unset/blank/garbage never enables the arm."""
    monkeypatch.delenv(ENRICH_ENV, raising=False)
    assert enrich_enabled() is False
    monkeypatch.setenv(ENRICH_ENV, "0")
    assert enrich_enabled() is False
    monkeypatch.setenv(ENRICH_ENV, "1")
    assert enrich_enabled() is True


# ── 2. The rewrite seam (unit) ─────────────────────────────────────────────

def test_enrich_turn_texts_is_identity_without_a_complete_fn():
    texts = ["[user] a", "[assistant] b"]
    assert enrich_turn_texts(texts) == texts


def test_enrich_turn_texts_rewrites_each_turn_with_its_neighbours():
    """The rewrite threads the neighbour window into each prompt."""
    texts = ["[user] it is broken", "[assistant] the auth flow broke"]
    calls: list[str] = []

    def fake(system, user):
        calls.append(user)
        target = user.split("TARGET turn (rewrite this one):\n", 1)[1]
        target = target.split("\n\n", 1)[0]
        return "RESOLVED: " + target

    out = enrich_turn_texts(texts, complete_fn=fake)
    assert out == ["RESOLVED: [user] it is broken",
                   "RESOLVED: [assistant] the auth flow broke"]
    # turn 0's prompt carries its NEXT neighbour; turn 1's carries its PREV.
    assert "[assistant] the auth flow broke" in calls[0]
    assert "[user] it is broken" in calls[1]


def test_enrich_turn_texts_fails_soft_to_raw_text():
    texts = ["[user] keep me"]

    def raising(system, user):
        raise RuntimeError("boom")

    assert enrich_turn_texts(texts, complete_fn=raising) == texts
    assert enrich_turn_texts(texts, complete_fn=lambda system, user: "   ") == texts
    assert enrich_turn_texts(texts, complete_fn=lambda system, user: None) == texts


# ── 3. OFF is byte-identical to pre-#8488 ──────────────────────────────────

def test_flag_off_writes_no_embedding_text_and_embeds_the_raw_content(
        sdk, embedder, monkeypatch):
    """OFF: no ``embeddingText`` node prop, vector == the raw stored text's."""
    monkeypatch.delenv(ENRICH_ENV, raising=False)
    _keyless(monkeypatch)
    sdk.capture_session(CONV, session_id="sess-off")
    rows = _turn_rows(sdk, "sess-off")
    assert len(rows) == len(CONV)
    for tid, content, enriched, stored in rows:
        assert enriched is None, (tid, "OFF must not write embeddingText")
        assert stored is not None
        assert np.allclose(stored, compute_embedding(content), atol=1e-6), tid


# ── 4. ON stores the enriched text and embeds IT (schema A′) ───────────────

def test_flag_on_stores_and_embeds_the_enriched_text(sdk, embedder, enrichment_on):
    """ON: ``embeddingText`` is stored and the ONE vector is computed from it."""
    sdk.capture_session(CONV, session_id="sess-on")
    rows = _turn_rows(sdk, "sess-on")
    assert len(rows) == len(CONV)
    t0, t1 = rows
    # turn 0's rewrite changed the text → its vector changed too (the #4194
    # point: the vector describes the ENRICHED text, never the raw content).
    assert t0[2] == "[user] the auth flow is blocked"
    assert t0[2] != t0[1]
    assert not np.allclose(t0[3], compute_embedding(t0[1]), atol=1e-6)
    assert np.allclose(t0[3], compute_embedding(t0[2]), atol=1e-6)
    # turn 1 is unchanged by this rewriter → embeddingText == content and the
    # vector is still that text's (both spellings agree).
    assert t1[2] == t1[1]
    assert np.allclose(t1[3], compute_embedding(t1[1]), atol=1e-6)


def test_journal_attests_the_enriched_text(sdk, embedder, enrichment_on, tmp_path):
    """``embedding_text_hash`` attests ``embeddingText``, not the raw content."""
    from tortoise.log import EventLog

    log_path = str(tmp_path / "events" / "sdk.jsonl")
    s = TortoiseSDK(str(tmp_path / "t.db"), event_log_path=log_path)
    try:
        s.capture_session(CONV, session_id="sess-attest")
    finally:
        s.close()
    events = EventLog(log_path).read_all()
    adds = [e for e in events if e.get("type") == "PointAdded"]
    assert adds, "no PointAdded journaled"
    enriched0 = adds[0]["point"]["embeddingText"]
    assert enriched0 == "[user] the auth flow is blocked"
    assert adds[0]["point"]["embedding_text_hash"] == content_hash(enriched0)


# ── 5. The on/off retrieval-equivalence property ───────────────────────────

def test_dense_leg_returns_the_enriched_turn_only_when_on(
        sdk, embedder, monkeypatch):
    """A test that cannot tell on from off is not a test: the SAME query
    surfaces a DIFFERENT top hit depending on the gate.

    The first turn's raw text says "the release is blocked"; enrichment
    resolves it to "the auth flow is blocked". Querying the ENRICHED meaning
    ("auth") must surface that turn only when the gate is ON — when OFF the
    turn's vector carries "release" and the auth query cannot find it.
    """
    _keyless(monkeypatch)
    monkeypatch.delenv(ENRICH_ENV, raising=False)
    sdk.capture_session(CONV, session_id="sess-rank-off")

    from tortoise import model_adapters
    monkeypatch.setenv(ENRICH_ENV, "1")
    monkeypatch.setattr(model_adapters, "build_extractor_model",
                        lambda *a, **kw: _Rewriter())
    sdk.capture_session(CONV, session_id="sess-rank-on")

    proj = sdk._get_proj()
    qvec = _query_vec(embedder, "auth")

    def scores(prefix):
        hits = run_vector_query(proj.g, qvec, limit=10,
                                is_embedded=getattr(proj, "_is_embedded", True),
                                entity_type="point")
        return {h[0]: h[1] for h in hits if h[0].startswith(prefix)}

    off = scores("sess-rank-off_")
    on = scores("sess-rank-on_")
    # The SAME first turn, raw ("release") vs enriched ("auth flow"): the
    # enriched twin must score strictly higher for the enriched query — and it
    # must reach its session's top. When OFF the raw turn's vector carries
    # "release", so the auth query barely scores it.
    assert on["sess-rank-on_t0"] > off["sess-rank-off_t0"], (off, on)
    assert on["sess-rank-on_t0"] == max(on.values()), on


# ── 6. The ON→OFF re-capture clears the stale enriched text ───────────────

def test_recapture_off_after_on_clears_the_stale_enriched_text(
        sdk, embedder, enrichment_on, monkeypatch):
    """A stale ``embeddingText`` must not survive a re-capture with the flag
    off — the node reverts to the raw-only shape, so no enriched text outlives
    the vector that described it."""
    sdk.capture_session(CONV, session_id="sess-recap")
    on_rows = _turn_rows(sdk, "sess-recap")
    assert all(r[2] is not None for r in on_rows), on_rows

    # The gate goes off; the SAME session is re-captured. The real
    # `_capture_enriched_texts` now returns None (the env is unset), so the
    # writer must REMOVE the stale enriched text, not preserve it.
    monkeypatch.delenv(ENRICH_ENV, raising=False)
    sdk.capture_session(CONV, session_id="sess-recap")
    off_rows = _turn_rows(sdk, "sess-recap")
    for tid, content, enriched, stored in off_rows:
        assert enriched is None, (tid, "a stale enriched text survived the OFF re-capture")
        assert stored is not None
        assert np.allclose(stored, compute_embedding(content), atol=1e-6), tid


# ── 7. Raw retrieval is unchanged ─────────────────────────────────────────

def test_content_stays_keyword_searchable_when_enriched(sdk, embedder, enrichment_on):
    """``content`` stays in the fulltext field set: a query that matched the
    raw text before still matches with enrichment on (the enriched text is a
    NEW field, never a replacement for ``content``)."""
    sdk.capture_session(CONV, session_id="sess-raw")
    hits = sdk.tortoise_fts_query("release", limit=5)
    assert any(h["id"].startswith("sess-raw_t") for h in hits), (
        f"raw retrieval lost the keyword the enriched text replaced: "
        f"{[h['id'] for h in hits]}")


def test_rebuild_preserves_the_enriched_text(sdk, embedder, enrichment_on, tmp_path):
    """live == replay for the new field: a rebuilt turn keeps its ``embeddingText``
    and the vector computed from it (the #4194 identity survives a rebuild)."""
    from tortoise.log import EventLog

    log_path = str(tmp_path / "events" / "sdk.jsonl")
    s = TortoiseSDK(str(tmp_path / "t.db"), event_log_path=log_path)
    try:
        s.capture_session(CONV, session_id="sess-rebuild")
        before = _turn_rows(s, "sess-rebuild")
        assert before and all(r[2] is not None for r in before), before

        s._get_proj().rebuild(EventLog(log_path), confirm_destructive=True)

        after = _turn_rows(s, "sess-rebuild")
        assert [r[0] for r in after] == [r[0] for r in before]
        for (bid, bc, be, bv), (aid, ac, ae, av) in zip(before, after, strict=True):
            assert aid == bid
            assert ae == be, (aid, "the rebuilt turn lost its embeddingText")
            assert np.allclose(bv, av, atol=1e-6), aid
            assert np.allclose(av, compute_embedding(ae), atol=1e-6), aid
    finally:
        s.close()


def test_attestation_prefers_embedding_text_when_present():
    """``stamp_journal_embedding`` attests ``embeddingText`` over ``content``."""
    from tortoise.embeddings import stamp_journal_embedding
    p = {"id": "x", "content": "raw", "embeddingText": "enriched",
         "embedding": [1.0]}
    stamp_journal_embedding(p, creating=True)
    assert p["embedding_text_hash"] == content_hash("enriched")
    q = {"id": "y", "content": "raw", "embedding": [1.0]}
    stamp_journal_embedding(q, creating=True)
    assert q["embedding_text_hash"] == content_hash("raw")
