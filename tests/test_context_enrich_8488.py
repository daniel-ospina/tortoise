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
from tortoise.embeddings import (
    NEAR_DUPLICATE_THRESHOLD,
    EmbeddingModel,
    compute_embedding,
)
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
    # …and on the RIGHT SIDE of the window (a prev/next swap would satisfy the
    # two assertions above, since both neighbours appear in both prompts).
    _MARK = "TARGET turn (rewrite this one):"
    left0 = calls[0].split(_MARK, 1)[0]
    assert left0.strip() == "Previous turn:", left0
    assert calls[1].split(_MARK, 1)[0].split("Previous turn:", 1)[1].strip() \
        == "[user] it is broken"
    assert calls[1].split("Next turn:", 1)[1].strip() == ""


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
        for (bid, _bc, be, bv), (aid, _ac, ae, av) in zip(before, after, strict=True):
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


# ── 8. The ruling's acceptance test #1: turn discrimination survives ───────

def _real_embedding_model_or_skip():
    """The PINNED embedder, or a skip (it needs `uv sync --extra embeddings`)."""
    EmbeddingModel._reset()
    model = EmbeddingModel.get()
    if model is None:
        pytest.skip("the pinned embedder is not installed here "
                    "(uv sync --extra embeddings)")
    return model


def _worst_pairwise_similarity(model, texts):
    vectors = np.asarray(model.encode(list(texts)), dtype=np.float64)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    unit = vectors / np.clip(norms, 1e-12, None)
    sims = unit @ unit.T
    np.fill_diagonal(sims, -1.0)
    return float(sims.max())


def test_turn_discrimination_survives_enrichment():
    """#8932 §3.1 — the ruling calls this "not optional".

    Enrichment must not merge neighbouring turns into near-duplicates: the
    DECLINED variant (one fixed session blurb prepended to every turn) fails
    exactly here, and that failure is why the approved shape is a rewrite. The
    band is this repo's own near-duplicate threshold for the pinned embedder
    (``NEAR_DUPLICATE_THRESHOLD = 0.89``), measured on the real model — the
    lexicon stand-in used elsewhere in this file cannot measure a 0.89 band.

    Residual (honest scope): #8932 §3.2 (a retired claim's enriched text must
    not keep describing the pre-retirement state) is covered for the field's
    LIFETIME by ``test_recapture_off_after_on_clears_the_stale_enriched_text``
    — an ON→OFF re-capture removes the value — but the claim-terminal path
    (#2490) is not exercised here; turns are episodic and are not superseded.
    """
    model = _real_embedding_model_or_skip()
    turns = [
        "[user] they said it blocks the release",
        "[user] the auth flow is the root cause",
        "[user] schedule the follow-up for friday",
        "[user] the billing migration lands next week",
    ]

    def rewrite(system, user):
        target = user.split("TARGET turn (rewrite this one):\n", 1)[1]
        return target.split("\n\n", 1)[0].replace("they said it", "it")

    enriched = enrich_turn_texts(turns, complete_fn=rewrite)
    assert enriched != turns, "the enrichment changed nothing — not a test"
    assert all(e != t for e, t in zip(enriched, turns, strict=True)), enriched

    raw = _worst_pairwise_similarity(model, turns)
    on = _worst_pairwise_similarity(model, enriched)
    assert on < NEAR_DUPLICATE_THRESHOLD, (
        f"enriched turns collapse into near-duplicates: max pairwise "
        f"similarity {on:.3f} >= {NEAR_DUPLICATE_THRESHOLD} (raw {raw:.3f})")

    # The CONTROL — the declined shape must be measurably worse ON THIS FIXTURE,
    # or the band assertion above is not evidence that the rewrite preserves
    # what a shared prefix destroys.
    blurb = "[session: the team is shipping the auth flow release this week] "
    prepended = [blurb + t for t in turns]
    assert _worst_pairwise_similarity(model, prepended) > on, (
        "the declined fixed-blurb variant did not raise within-session "
        "similarity above the approved rewrite on this fixture")


# ── 9. The preserve arm is LOAD-BEARING (review round 2, P2) ───────────────

def _no_embedder(monkeypatch):
    """The documented `_capture_turn_embeddings` no-vector path: the embedder is
    unavailable for the whole capture, so `turn.emb` is NULL and the write's
    ``CASE`` reaches its preserve/clear arm instead of the new-vector branch."""
    monkeypatch.setattr(EmbeddingModel, "get", classmethod(
        lambda cls, load_timeout=None: None))
    EmbeddingModel._reset()


def test_recapture_on_with_a_different_rewrite_clears_the_stale_vector(
        sdk, embedder, enrichment_on, monkeypatch):
    """#4194 on the preserve arm: with no new vector, the stored one may be kept
    ONLY while the text it describes is unchanged. The first test in this file
    that would red if the `prior_et` half of that guard were removed."""
    sdk.capture_session(CONV, session_id="sess-on2")
    on_rows = _turn_rows(sdk, "sess-on2")
    assert all(r[2] is not None for r in on_rows), on_rows
    assert all(r[3] is not None for r in on_rows), on_rows

    # The embedder disappears (the keyword-only install), and the rewrite
    # changes: turn 0 becomes "the billing flow is blocked". The stored vector
    # describes the OLD embeddingText, so it must be CLEARED, not preserved.
    from tortoise import model_adapters

    class _BillingRewriter:
        def complete(self, *, system, user):
            target = user.split("TARGET turn (rewrite this one):\n", 1)[1]
            target = target.split("\n\n", 1)[0]
            return target.replace("the release is blocked",
                                  "the billing flow is blocked")

    _no_embedder(monkeypatch)
    monkeypatch.setattr(model_adapters, "build_extractor_model",
                        lambda *a, **kw: _BillingRewriter())
    sdk.capture_session(CONV, session_id="sess-on2")

    rows = _turn_rows(sdk, "sess-on2")
    t0 = rows[0]
    assert t0[2] == "[user] the billing flow is blocked", t0
    assert t0[3] is None, (
        "the stored vector describes the PREVIOUS embeddingText and was "
        "preserved beside a new one — the #4194 lie (prior_et guard missing)")


def test_content_edit_clears_the_enriched_text(sdk, embedder, enrichment_on):
    """`update_point(content=…)` re-derives the vector from `content`, so the
    node must not keep an `embeddingText` no vector was computed from
    (review round 2, P1 — the other write door into the same #4194 rule)."""
    sdk.capture_session(CONV, session_id="sess-edit")
    t0 = _turn_rows(sdk, "sess-edit")[0]
    assert t0[2] == "[user] the auth flow is blocked", t0

    sdk.update_point(t0[0], content="the billing migration is blocked")
    after = _turn_rows(sdk, "sess-edit")[0]
    assert after[2] is None, (
        "a stale embeddingText survived a content edit that re-encoded the "
        "vector from the new content (#4194)")
    assert np.allclose(after[3], compute_embedding("the billing migration is blocked"),
                       atol=1e-6), after
